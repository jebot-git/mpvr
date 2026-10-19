#!/usr/bin/env python3
"""Render actual mpv frames on private Xvfb and inspect window screenshots."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('mpvr', ROOT / 'xr/mpvr.py')
mpvr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mpvr)
ART = ROOT / 'artifacts/xr'; ART.mkdir(parents=True, exist_ok=True)


def ipc(file, command):
    file.write(json.dumps({'command': command}).encode() + b'\n')
    while True:
        value = json.loads(file.readline())
        if 'error' in value:
            if value['error'] in ('property not found', 'property unavailable'): return None
            assert value['error'] == 'success', value
            return value.get('data')


def run_case(directory, env, name, width, height, layout='auto', swap=False, metadata=None, par=None, subtitles=False):
    media = directory / f'{name}.mkv'
    image = np.zeros((height, width, 3), np.uint8)
    image[:, :width // 2] = (0, 0, 255)
    image[:, width // 2:] = (255, 0, 0)
    if name == 'ordinary': image[:] = (0, 0, 255)
    args = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
            '-s', f'{width}x{height}', '-r', '24', '-i', '-', '-f', 'lavfi',
            '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2', '-c:v', 'ffv1', '-c:a', 'pcm_s16le']
    if metadata: args += ['-metadata:s:v:0', f'stereo_mode={metadata}']
    if par: args += ['-vf', f'setsar={par}']
    subprocess.run([*args, str(media)], input=image.tobytes() * 48, check=True)
    path = directory / f'{name}.socket'
    player_args = ['--no-config', '--gpu-sw=yes', '--ao=null', '--pause', f'--log-file={ART / (name + "-native.log")}', '--hwdec=no', '--loop-file=inf']
    if subtitles:
        sub = directory / 'test.srt'; sub.write_text('1\n00:00:00,000 --> 00:00:02,000\nSTEREO SUBTITLE\n')
        player_args += [f'--sub-file={sub}', '--sub-font-size=30', '--sub-color=#FFFFFFFF']
    args = mpvr.video_args([*player_args, str(media)], '640x360', name, path, layout, swap, 'x11egl')
    log = (ART / f'{name}.log').open('w')
    child = subprocess.Popen([str(ROOT / 'build/mpv'), *args], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        sock = mpvr.connect_ipc(path, child)
        sock.settimeout(10)
        with sock, sock.makefile('rwb', buffering=0) as file:
            deadline = time.monotonic() + 15
            state = None
            while time.monotonic() < deadline:
                state = ipc(file, ['get_property', 'user-data/mpvr'])
                if state: break
                time.sleep(.1)
            assert state, 'Lua did not configure shader'
            time.sleep(.3)
            frame = ART / f'{name}.png'
            ipc(file, ['screenshot-to-file', str(frame), 'window'])
            rgb = cv2.imread(str(frame))[:, :, ::-1]
            h, w = rgb.shape[:2]
            left, right = rgb[h // 2, w // 4], rgb[h // 2, 3 * w // 4]
            if name == 'ordinary':
                assert left[0] > 180 and right[0] > 180, (left, right)
            elif swap or metadata == 'right_left':
                assert left[2] > 180 and right[0] > 180, (name, left, right, state)
            else:
                assert left[0] > 180 and right[2] > 180, (name, left, right, state)
            # Full 16:9 and half 16:9 pairs must fill each stretched eye.
            if name in ('movie_SBS', 'movie_HSBS', 'metadata', 'right_metadata', 'explicit_metadata', 'half_metadata'):
                assert rgb[h // 8, w // 4].max() > 180, (name, state)
                assert rgb[h // 2, w // 32].max() > 180, (name, 'unexpected pillarbox', state)
            if 'portrait' in name:
                assert rgb[h // 2, w // 32].max() < 10, 'Missing independent eye margins'
            if subtitles:
                mask = (rgb.min(axis=2) > 180)
                l, r = mask[:, :w // 2], mask[:, w // 2:]
                assert l.sum() > 20 and np.array_equal(l, r), 'Subtitle is not duplicated per eye'
            ipc(file, ['set_property', 'pause', False])
            time.sleep(.4)
            assert ipc(file, ['get_property', 'time-pos']) > .1
            ipc(file, ['seek', .75, 'absolute'])
            time.sleep(.2)
            assert ipc(file, ['get_property', 'time-pos']) >= .7
            ipc(file, ['quit'])
            assert child.wait(timeout=5) == 0
            return dict(name=name, state=state, left=left.tolist(), right=right.tolist(), seek=True, audio=True)
    finally:
        if child.poll() is None: child.terminate(); child.wait(timeout=5)
        log.close()


def main():
    with tempfile.TemporaryDirectory(prefix='mpvr-render-') as temporary:
        directory = Path(temporary)
        xvfb = shutil.which('Xvfb') or str(Path.home() / 'DomeD/.local-deps/root/usr/bin/Xvfb')
        rd, wr = os.pipe()
        x = subprocess.Popen([xvfb, '-displayfd', str(wr), '-screen', '0', '1280x720x24', '-nolisten', 'tcp'], pass_fds=(wr,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(wr)
        try:
            with os.fdopen(rd) as pipe: display = ':' + pipe.readline().strip()
            env = dict(os.environ, DISPLAY=display, LIBGL_ALWAYS_SOFTWARE='1')
            env.pop('WAYLAND_DISPLAY', None)
            reports = []
            reports.append(run_case(directory, env, 'ordinary', 320, 180))
            reports.append(run_case(directory, env, 'movie_SBS', 640, 180))
            reports.append(run_case(directory, env, 'movie_HSBS', 320, 180))
            reports.append(run_case(directory, env, 'portrait_SBS', 180, 240))
            reports.append(run_case(directory, env, 'swap_SBS', 640, 180, swap=True))
            reports.append(run_case(directory, env, 'metadata', 640, 180, metadata='left_right'))
            reports.append(run_case(directory, env, 'right_metadata', 640, 180, metadata='right_left'))
            reports.append(run_case(directory, env, 'explicit_metadata', 640, 180, layout='full-sbs', metadata='right_left'))
            reports.append(run_case(directory, env, 'half_metadata', 320, 180, metadata='left_right', par='2'))
            reports.append(run_case(directory, env, 'subtitle_SBS', 640, 180, subtitles=True))
            (ART / 'report.json').write_text(json.dumps(reports, indent=2))
            print(json.dumps(reports))
        finally:
            x.terminate(); x.wait(timeout=5)


if __name__ == '__main__': main()
