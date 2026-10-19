import base64
import importlib.util
import json
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
            with self.assertRaisesRegex(RuntimeError, 'not connected'): mpvr.main(['--xr-output=wayvr', 'a.mkv'])

    def test_odd_surface_width_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'even'): mpvr.main(['--xr-size=641x360', 'a.mkv'])


if __name__ == '__main__': unittest.main()
