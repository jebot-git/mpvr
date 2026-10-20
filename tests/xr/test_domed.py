#!/usr/bin/env python3
"""Actual Wayland playback in a private DomeD with simulated XR heartbeat."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import cv2
import numpy as np

if os.environ.get('MPVR_TEST_BUS') != '1':
    os.execvpe('dbus-run-session', ['dbus-run-session', '--', sys.executable, *sys.argv], dict(os.environ, MPVR_TEST_BUS='1'))
ROOT = Path(__file__).resolve().parents[2]
DOME = Path(os.environ.get('MPVR_DOME_SOURCE', str(Path.home() / 'DomeD')))
ART = ROOT / 'artifacts/domed'; ART.mkdir(parents=True, exist_ok=True)
children, logs = [], []
stop = threading.Event()
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path, value=None):
    req = urllib.request.Request(f'http://127.0.0.1:{port}' + path,
        data=None if value is None else json.dumps(value).encode(), headers={'Content-Type': 'application/json'})
    return http.open(req, timeout=3)


def scene():
    with request('/api/state') as response: return json.load(response)['scene']


def wait(check, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result is not None and result is not False: return result
        except (OSError, ValueError, KeyError, StopIteration): pass
        time.sleep(.1)
    raise AssertionError('DomeD integration timed out; inspect artifacts/domed')


def start(args, name, env, **kwargs):
    log = (ART / (name + '.log')).open('w'); logs.append(log)
    child = subprocess.Popen(list(map(str, args)), env=env, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    children.append(child); return child


try:
    with tempfile.TemporaryDirectory(prefix='mpvr-dome-') as directory:
        runtime = Path(directory); runtime.chmod(0o700)
        rd, wr = os.pipe()
        xvfb = shutil.which('Xvfb') or str(DOME / '.local-deps/root/usr/bin/Xvfb')
        start([xvfb, '-displayfd', wr, '-screen', '0', '1280x720x24', '-nolisten', 'tcp'], 'xvfb', os.environ.copy(), pass_fds=(wr,))
        os.close(wr)
        with os.fdopen(rd) as pipe: display = ':' + pipe.readline().strip()
        env = dict(os.environ, DISPLAY=display, XDG_RUNTIME_DIR=directory, TMPDIR=directory,
            XDG_CONFIG_HOME=str(runtime / 'config'), XDG_DATA_HOME=str(runtime / 'data'),
            DOME_PREVIEW_PORT=str(port), DOME_START_XR='0', LIBGL_ALWAYS_SOFTWARE='1',
            RUST_LOG='error', XR_RUNTIME_JSON=str(runtime / 'no-runtime.json'))
        env.pop('WAYLAND_DISPLAY', None); env.pop('WAYLAND_SOCKET', None)
        # System updates replace the executable while a login session keeps
        # running. Linux then appends " (deleted)" to /proc/PID/exe.
        compositor_binary = runtime / 'dome-compositor'
        compositor_source = os.environ.get('MPVR_DOME_BINARY', str(DOME / 'target/debug/dome-compositor'))
        shutil.copy2(compositor_source, compositor_binary)
        compositor = start([compositor_binary, '--backend', 'nested'], 'compositor', env)
        wait(scene)
        records = list(runtime.glob('*.dome-session.json'))
        record = next(p for p in records if json.loads(p.read_text())['realm'] == 'desktop')
        env['WAYLAND_DISPLAY'] = str(record).removesuffix('.dome-session.json')
        def heartbeat():
            while not stop.wait(.2):
                try: request('/api/xr-session', {'id': 992, 'visible': True, 'focused': True}).close()
                except OSError: pass
        worker = threading.Thread(target=heartbeat, daemon=True); worker.start()
        wait(lambda: json.load(request('/api/integrations/mpvr'))['xr_active'])
        compositor_binary.unlink()
        shutil.copy2(compositor_source, compositor_binary)
        assert os.readlink(f'/proc/{compositor.pid}/exe').endswith(' (deleted)')
        media = runtime / 'video #ü_SBS.mkv'
        source = np.zeros((180, 640, 3), np.uint8)
        source[:, :320] = (0, 0, 255); source[:, 320:] = (255, 0, 0)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
            '-s', '640x180', '-r', '24', '-i', '-', '-c:v', 'ffv1', str(media)], input=source.tobytes()*24, check=True)
        player = start([os.environ.get('MPVR_LAUNCHER', str(ROOT / 'xr/mpvr.py')), '--xr-output=domed', '--xr-size=640x360', '--no-config', '--gpu-sw=yes',
                        '--ao=null', '--pause', '--loop-file=inf', str(media)], 'player', env)
        window = wait(lambda: next((w for w in scene()['windows'] if w['app_id'] == 'org.mpvr.Sbs'), None))
        assert window['origin'] == 'xr' and window['stereo'] == 'side_by_side'
        assert window['desktop_visible'] is False
        def pixels():
            with request(f'/api/xr-frame/{window["id"]}') as response:
                width, height = int(response.headers['X-Dome-Width']), int(response.headers['X-Dome-Height'])
                frame = np.frombuffer(response.read(), np.uint8).reshape(height, width, 4)
            h, w = frame.shape[:2]
            if frame[h//2,w//4,0] > 180 and frame[h//2,3*w//4,2] > 180: return frame
        frame = wait(pixels)
        cv2.imwrite(str(ART / 'surface.png'), cv2.cvtColor(frame, cv2.COLOR_RGBA2BGRA))
        player.terminate(); player.wait(timeout=8)
        wait(lambda: not any(w['app_id']=='org.mpvr.Sbs' for w in scene()['windows']))
        stop.set(); worker.join(timeout=3)
        wait(lambda: not json.load(request('/api/integrations/mpvr'))['xr_active'])
        inactive = start([os.environ.get('MPVR_LAUNCHER', str(ROOT / 'xr/mpvr.py')), '--domed', str(media)], 'inactive', env)
        assert inactive.wait(timeout=8) == 2
        report = dict(xr_only_window=True, stereo='side_by_side', distinct_eye_pixels=True,
                      cleanup=True, inactive_error=True, unicode_path=True,
                      executable_replaced=True, headset_verified=False)
        (ART / 'report.json').write_text(json.dumps(report, indent=2)); print(json.dumps(report))
finally:
    stop.set()
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
    for log in logs: log.close()
