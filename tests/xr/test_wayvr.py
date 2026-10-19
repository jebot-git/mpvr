#!/usr/bin/env python3
"""Opt-in live test: creates and cleans only its own WayVR overlay."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
import cv2

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('mpvr', ROOT / 'xr/mpvr.py')
mpvr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mpvr)
ART = ROOT / 'artifacts/wayvr'; ART.mkdir(parents=True, exist_ok=True)
assert mpvr.headset_connected(), 'Connect WiVRn before running this optional test'
media = ART / 'live video #ü_SBS.mkv'
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:s=960x540:r=24',
    '-f', 'lavfi', '-i', 'color=blue:s=960x540:r=24', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
    '-filter_complex', '[0:v][1:v]hstack[v]', '-map', '[v]', '-map', '2:a', '-t', '2',
    '-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(media)], check=True)
log = (ART / 'launcher.log').open('w')
child = subprocess.Popen([os.environ.get('MPVR_LAUNCHER', str(ROOT / 'xr/mpvr.py')), '--xr-output=wayvr', '--no-config', '--pause', '--mute=yes',
    '--loop-file=inf', f'--log-file={ART / "native.log"}', str(media)], stdout=log, stderr=subprocess.STDOUT)
name = f'mpvr-sbs-{child.pid}'
try:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        paths = list(Path('/tmp').glob(f'mpvr-{child.pid}-*/ipc'))
        if paths: break
        assert child.poll() is None, 'Launcher failed; inspect artifacts/wayvr'
        time.sleep(.1)
    assert paths
    sock = mpvr.connect_ipc(paths[0]); sock.settimeout(8)
    with sock, sock.makefile('rwb', buffering=0) as file:
        def ipc(command):
            file.write(json.dumps({'command': command}).encode() + b'\n')
            while True:
                data = json.loads(file.readline())
                if 'error' in data:
                    assert data['error']=='success', data
                    return data.get('data')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if name in mpvr.control(mpvr.wayvr_controller(), 'window-list').splitlines():
                stereo = mpvr.control(mpvr.wayvr_controller(), 'window-attrib', name, 'get', 'stereo')
                if 'left_right' in stereo: break
            time.sleep(.1)
        assert 'left_right' in stereo
        time.sleep(.5)
        state = ipc(['get_property', 'user-data/mpvr'])
        assert state['layout'] == 'full-sbs', state
        frame = ART / 'surface.png'
        ipc(['screenshot-to-file', str(frame), 'window'])
        rgb = cv2.imread(str(frame))[:, :, ::-1]; h,w = rgb.shape[:2]
        assert rgb[h//2,w//4,0] > 180 and rgb[h//2,3*w//4,2] > 180
        ipc(['set_property', 'pause', False]); time.sleep(.5)
        assert ipc(['get_property', 'time-pos']) > .1
        hwdec = ipc(['get_property', 'hwdec-current'])
        full_frame = mpvr.control(mpvr.wayvr_controller(), 'window-attrib', name, 'get', 'stereo_full_frame')
        ipc(['quit'])
        assert child.wait(timeout=12) == 0
        assert name not in mpvr.control(mpvr.wayvr_controller(), 'window-list').splitlines()
        report = dict(wayvr=True, stereo=stereo, stereo_full_frame=full_frame, state=state, hwdec=hwdec,
                      distinct_eye_pixels=True, playback=True, cleanup=True, headset_perception_verified=False)
        (ART / 'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report))
finally:
    if child.poll() is None: child.terminate(); child.wait(timeout=12)
    log.close()
