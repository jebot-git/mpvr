#!/usr/bin/env python3
"""Verify GUI startup and rendered native controls in the installed player."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import cv2
import numpy as np
from test_playback import mpvr, ROOT, ipc

ART = ROOT / 'artifacts/ui'; ART.mkdir(parents=True, exist_ok=True)
LAUNCHER = os.environ.get('MPVR_LAUNCHER', str(Path.home() / '.local/bin/mpvr'))


def wait(check, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value: return value
        time.sleep(.05)
    raise AssertionError('UI did not become ready; inspect artifacts/ui')


with tempfile.TemporaryDirectory(prefix='mpvr-ui-') as directory:
    temporary = Path(directory)
    rd, wr = os.pipe()
    xvfb = shutil.which('Xvfb') or str(Path.home() / 'DomeD/.local-deps/root/usr/bin/Xvfb')
    x = subprocess.Popen([xvfb, '-displayfd', str(wr), '-screen', '0', '1280x720x24', '-nolisten', 'tcp'],
                         pass_fds=(wr,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(wr)
    child = None
    try:
        with os.fdopen(rd) as pipe: display = ':' + pipe.readline().strip()
        env = dict(os.environ, DISPLAY=display, LIBGL_ALWAYS_SOFTWARE='1')
        env.pop('WAYLAND_DISPLAY', None); env.pop('WAYLAND_SOCKET', None)
        media = temporary / 'ui video.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=0x205070:s=960x540:r=24',
                        '-t', '2', '-c:v', 'ffv1', str(media)], check=True)
        path = temporary / 'ipc'
        with (ART / 'player.log').open('w') as log:
            child = subprocess.Popen([LAUNCHER, '--xr-output=off', '--player-operation-mode=pseudo-gui',
                '--no-config', '--gpu-sw=yes', '--vo=gpu-next', '--gpu-api=opengl', '--gpu-context=x11egl',
                '--geometry=960x540', '--ao=null', '--pause', '--loop-file=inf',
                f'--input-ipc-server={path}', f'--log-file={ART / "native.log"}'], env=env, stdout=log, stderr=subprocess.STDOUT)
            sock = mpvr.connect_ipc(path, child); sock.settimeout(10)
            with sock, sock.makefile('rwb', buffering=0) as file:
                get = lambda name: ipc(file, ['get_property', name])
                wait(lambda: get('current-vo'))
                assert get('idle-active') is True
                assert get('osc') is True
                assert get('load-console') is True and get('load-select') is True
                assert get('load-context-menu') is True
                wait(lambda: (get('osd-dimensions') or {}).get('w', 0) > 0)
                ipc(file, ['loadfile', str(media), 'replace'])
                wait(lambda: get('video-out-params/w') == 960)
                wait(lambda: get('user-data/osc/visibility') == 'auto')
                def shot(name):
                    time.sleep(.25)
                    target = ART / f'{name}.png'
                    ipc(file, ['screenshot-to-file', str(target), 'window'])
                    return cv2.imread(str(target))
                ipc(file, ['script-message-to', 'osc', 'osc-visibility', 'never', 'yes'])
                wait(lambda: get('user-data/osc/visibility') == 'never')
                plain = shot('plain')
                ipc(file, ['script-message-to', 'osc', 'osc-visibility', 'always', 'yes'])
                wait(lambda: get('user-data/osc/visibility') == 'always')
                controls = shot('controls')
                changed = np.max(np.abs(controls.astype(int)-plain.astype(int)), axis=2)>25
                assert changed[-120:].sum() > 1000, 'OSC is not visible'
                # Default bottom-bar play/pause button at the left of the controls.
                h,w = controls.shape[:2]
                ipc(file, ['mouse', 14, h-12])
                time.sleep(.15)
                ipc(file, ['keydown', 'MBTN_LEFT'])
                time.sleep(.1)
                ipc(file, ['keyup', 'MBTN_LEFT'])
                wait(lambda: get('pause') is False)
                ipc(file, ['set_property', 'pause', True])
                ipc(file, ['script-message-to', 'context_menu', 'open'])
                wait(lambda: get('user-data/mpv/context-menu/open') is True)
                menu = shot('menu')
                assert (np.max(np.abs(menu.astype(int)-controls.astype(int)), axis=2)>25).sum() > 300
                ipc(file, ['keypress', 'ESC'])
                wait(lambda: get('user-data/mpv/context-menu/open') is False)
                ipc(file, ['script-binding', 'console/enable'])
                console = shot('console')
                assert (np.max(np.abs(console.astype(int)-controls.astype(int)), axis=2)>25).sum() > 300
                ipc(file, ['quit']); assert child.wait(timeout=5) == 0
        with (ART / 'stereo.log').open('w') as log:
            child = subprocess.Popen([LAUNCHER, '--xr-output=desktop', '--sbs-layout=full-sbs',
                '--xr-size=960x540', '--no-config', '--gpu-sw=yes', '--pause', '--ao=null',
                '--loop-file=inf', str(media)], env=env, stdout=log, stderr=subprocess.STDOUT)
            paths = wait(lambda: list(Path('/tmp').glob(f'mpvr-{child.pid}-*/ipc')))
            sock = mpvr.connect_ipc(paths[0], child); sock.settimeout(10)
            with sock, sock.makefile('rwb', buffering=0) as file:
                get = lambda name: ipc(file, ['get_property', name])
                wait(lambda: get('user-data/mpvr'))
                assert get('osc') is False and get('load-console') is True
                time.sleep(.25)
                before = ART / 'stereo.png'
                ipc(file, ['screenshot-to-file', str(before), 'window'])
                ipc(file, ['script-binding', 'console/enable'])
                time.sleep(.25)
                after = ART / 'stereo-console.png'
                ipc(file, ['screenshot-to-file', str(after), 'window'])
                diff = np.max(np.abs(cv2.imread(str(after)).astype(int)-cv2.imread(str(before)).astype(int)), axis=2)>25
                midpoint = diff.shape[1]//2
                assert diff[:, :midpoint].sum()>300 and diff[:, midpoint:].sum()>300
                ipc(file, ['keypress', 'ESC'])
                time.sleep(.1)
                ipc(file, ['keypress', 'SPACE'])
                wait(lambda: get('pause') is False)
                ipc(file, ['quit']); assert child.wait(timeout=8) == 0
        report = dict(gui_idle_window=True, lua_ui=True, osc_rendered=True, mouse_play_pause=True,
                      context_menu=True, console=True, installed_player=True,
                      stereo_console_in_both_eyes=True, stereo_keyboard_controls=True)
        (ART / 'report.json').write_text(json.dumps(report, indent=2)); print(json.dumps(report))
    finally:
        if child and child.poll() is None: child.terminate(); child.wait(timeout=5)
        x.terminate(); x.wait(timeout=5)
