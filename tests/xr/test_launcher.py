import base64
import importlib.util
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('mpvr', ROOT / 'xr/mpvr.py')
mpvr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mpvr)


class LauncherTests(unittest.TestCase):
    def test_encoded_paths_and_options_are_lossless(self):
        args = ['--title=a b', '/tmp/video # ü_SBS.mkv', '--sub-file=/tmp/sub title.srt']
        self.assertEqual(json.loads(base64.urlsafe_b64decode(mpvr.encode_args(args))), args)

    def test_managed_options_precede_filename_separator(self):
        args = mpvr.video_args(['--pause', '--', '-video ü.mkv'], '640x360', 'test', '/tmp/ipc', 'auto', False)
        self.assertEqual(args[-2:], ['--', '-video ü.mkv'])
        self.assertLess(args.index('--vo=gpu-next'), args.index('--'))

    def test_off_and_introspection_do_not_contact_xr(self):
        for args in (['--xr-output=off', 'a.mkv'], ['--version'], ['--vo=help']):
            with patch.object(mpvr, 'domed_status') as dome, patch.object(mpvr, 'headset_connected') as headset, patch.object(mpvr.os, 'execv') as execute:
                # execv never returns in production.
                execute.side_effect = SystemExit
                with self.assertRaises(SystemExit): mpvr.main(args)
                dome.assert_not_called(); headset.assert_not_called()

    def test_inactive_dome_stays_on_desktop(self):
        with patch.object(mpvr, 'domed_status', return_value={'socket': 'dome-xr', 'active': False}), patch.object(mpvr, 'headset_connected') as headset, patch.object(mpvr.os, 'execv', side_effect=SystemExit):
            with self.assertRaises(SystemExit): mpvr.main(['a.mkv'])
            headset.assert_not_called()

    def test_unavailable_explicit_dome_errors(self):
        with patch.object(mpvr, 'domed_status', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'No active'): mpvr.main(['--domed', 'a.mkv'])

    def test_unavailable_wayvr_errors(self):
        with patch.object(mpvr, 'headset_connected', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'not connected'): mpvr.main(['--xr-output=wayvr', '--sbs-layout=full-sbs', 'a.mkv'])

    def test_odd_surface_width_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'even'): mpvr.main(['--xr-size=641x360', 'a.mkv'])

    def test_empty_gui_starts_desktop_without_contacting_xr(self):
        args = ['--player-operation-mode=pseudo-gui', '--']
        with patch.object(mpvr, 'domed_status') as dome, patch.object(mpvr, 'headset_connected') as headset, patch.object(mpvr.os, 'execv', side_effect=SystemExit) as execute:
            with self.assertRaises(SystemExit): mpvr.main(args)
            dome.assert_not_called(); headset.assert_not_called()
            self.assertEqual(execute.call_args.args[1][1:], args)

    def test_source_metadata_precedes_filename(self):
        for mode, expected in [('left_right', True), ('right_left', True), ('mono', False), ('top_bottom', False)]:
            payload = {'streams': [{'tags': {'stereo_mode': mode}}]}
            with patch.object(mpvr.subprocess, 'run') as probe:
                probe.return_value.stdout = json.dumps(payload)
                self.assertEqual(mpvr.recognize_video('/tmp/marked_SBS.mkv')[0], expected)

    def test_frame_side_data_is_recognized(self):
        self.assertEqual(mpvr.stereo_metadata({'frames': [{'side_data_list': [
            {'side_data_type': 'Stereo 3D', 'type': 'side by side', 'inverted': 1}]}]}), 'sbs')

    def test_wide_or_3d_named_monoscopic_video_stays_desktop(self):
        with patch.object(mpvr.subprocess, 'run') as probe:
            probe.return_value.stdout = json.dumps({'streams': [{'width': 7680, 'height': 1080}]})
            for name in ('panorama.mkv', 'movie_3D.mp4', 'SBS_folder/photo.mp4'):
                self.assertFalse(mpvr.recognize_video(name)[0])

    def test_markers_fallback_when_probe_unavailable(self):
        with patch.object(mpvr.subprocess, 'run', side_effect=OSError):
            for name in ('a_SBS.mkv', 'a.HSBS.mp4', 'a_FULL-SBS.mkv', 'a_half_sbs.mp4'):
                self.assertTrue(mpvr.recognize_video(name)[0], name)

    def test_mixed_and_opaque_playlists_use_desktop(self):
        with patch.object(mpvr, 'recognize_video', side_effect=lambda path, _: (path.startswith('3d'), 'test')):
            self.assertTrue(mpvr.only_stereo_sources(['--pause', '3d-a.mkv', '3d-b.mkv'], 'auto'))
            self.assertFalse(mpvr.only_stereo_sources(['3d-a.mkv', '2d-b.mkv'], 'auto'))
            self.assertFalse(mpvr.only_stereo_sources(['--playlist=3d.m3u'], 'auto'))
        self.assertTrue(mpvr.only_stereo_sources(['unmarked.mkv'], 'full-sbs'))
        self.assertFalse(mpvr.only_stereo_sources(['3d_SBS.mkv'], 'mono'))

    def test_2d_auto_on_connected_headset_executes_normal_player(self):
        with patch.object(mpvr, 'domed_status', return_value=None), patch.object(mpvr, 'headset_connected', return_value=True), patch.object(mpvr, 'only_stereo_sources', return_value=False), patch.object(mpvr.os, 'execv', side_effect=SystemExit) as execute:
            with self.assertRaises(SystemExit): mpvr.main(['--pause', '2d.mkv'])
            self.assertEqual(execute.call_args.args[1][1:], ['--pause', '2d.mkv'])

    def test_runtime_2d_handoff_starts_desktop_player(self):
        from unittest.mock import Mock
        sock = Mock()
        event = {'event': 'property-change', 'id': 991, 'data': {'desktop': True, 'layout': 'mono',
                 'path': '/tmp/2D video ü.mkv', 'start': .75}}
        sock.makefile.return_value = io.BytesIO(json.dumps(event).encode() + b'\n')
        with patch.object(mpvr.subprocess, 'Popen') as launch:
            mpvr.monitor_playback(sock)
            self.assertEqual(launch.call_args.args[0][-3:], ['--start=0.75', '--', '/tmp/2D video ü.mkv'])


if __name__ == '__main__': unittest.main()
