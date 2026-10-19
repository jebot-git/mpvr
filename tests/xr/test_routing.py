#!/usr/bin/env python3
"""Live non-DomeD routing: 2D desktop, metadata-only 3D XR, 2D handoff."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from test_playback import mpvr, ROOT, ipc

ART = ROOT / 'artifacts/routing'; ART.mkdir(parents=True, exist_ok=True)
assert mpvr.headset_connected(), 'This optional live test requires connected WiVRn'
assert mpvr.domed_status() is None, 'Run this test outside DomeD'
LAUNCHER = os.environ.get('MPVR_LAUNCHER', str(ROOT / 'xr/mpvr.py'))
mono = ART / 'ordinary 2D ü.mkv'
stereo = ART / 'metadata-only ü.mkv'
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=green:s=640x360:r=24',
                '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(mono)], check=True)
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:s=640x360:r=24',
    '-f', 'lavfi', '-i', 'color=blue:s=640x360:r=24', '-filter_complex', 'hstack', '-t', '3',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-metadata:s:v:0', 'stereo_mode=left_right', str(stereo)], check=True)
assert mpvr.recognize_video(str(mono))[0] is False
assert mpvr.recognize_video(str(stereo))[0] is True
ctl = mpvr.wayvr_controller()
children = []; desktop_pid = None
with tempfile.TemporaryDirectory(prefix='mpvr-routing-') as directory, (ART / 'desktop.log').open('w') as log:
    path = Path(directory) / 'ipc'
    child = subprocess.Popen([LAUNCHER, '--xr-output=auto', '--no-config', '--pause', '--mute=yes',
        f'--input-ipc-server={path}', str(mono)], stdout=log, stderr=subprocess.STDOUT)
    children.append(child)
    try:
        sock = mpvr.connect_ipc(path, child); sock.settimeout(8)
        with sock, sock.makefile('rwb', buffering=0) as file:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                mode = ipc(file, ['get_property', 'video-out-params/stereo-in'])
                if mode: break
                time.sleep(.1)
            assert mode == 'mono', mode
            cmdline = Path(f'/proc/{child.pid}/cmdline').read_bytes().split(b'\0')
            assert not any(b'mpvr.lua' in arg for arg in cmdline), cmdline
            assert f'mpvr-sbs-{child.pid}' not in mpvr.control(ctl, 'window-list').splitlines()
            ipc(file, ['quit']); assert child.wait(timeout=8) == 0
        with (ART / 'xr.log').open('w') as xrlog:
            xr = subprocess.Popen([LAUNCHER, '--xr-output=auto', '--no-config', '--pause', '--mute=yes',
                                   '--loop-file=inf', str(stereo)], stdout=xrlog, stderr=subprocess.STDOUT)
            children.append(xr)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                sockets = list(Path('/tmp').glob(f'mpvr-{xr.pid}-*/ipc'))
                if sockets: break
                assert xr.poll() is None
                time.sleep(.1)
            assert sockets
            sock = mpvr.connect_ipc(sockets[0]); sock.settimeout(10)
            with sock, sock.makefile('rwb', buffering=0) as file:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if 'wayvr SBS output' in (ART / 'xr.log').read_text(): break
                    time.sleep(.1)
                state = ipc(file, ['get_property', 'user-data/mpvr'])
                assert state['layout'] == 'metadata-sbs', state
                ipc(file, ['loadfile', str(mono), 'replace'])
                assert xr.wait(timeout=12) == 0
            text = (ART / 'xr.log').read_text()
            match = re.search(r'2D content moved to desktop \(PID (\d+)\)', text)
            assert match, text
            desktop_pid = int(match[1])
            cmdline = Path(f'/proc/{desktop_pid}/cmdline').read_bytes().split(b'\0')
            assert str(mono).encode() in cmdline and not any(b'mpvr.lua' in arg for arg in cmdline)
            assert f'mpvr-sbs-{xr.pid}' not in mpvr.control(ctl, 'window-list').splitlines()
            report = dict(connected_headset=True, mono_on_desktop=True, metadata_only_sbs_in_xr=True,
                          runtime_mono_handoff=True, xr_cleanup=True)
            (ART / 'report.json').write_text(json.dumps(report, indent=2)); print(json.dumps(report))
    finally:
        for child in children:
            if child.poll() is None: child.terminate(); child.wait(timeout=10)
        if desktop_pid:
            try:
                if str(mono).encode() in Path(f'/proc/{desktop_pid}/cmdline').read_bytes():
                    os.kill(desktop_pid, signal.SIGTERM)
            except (FileNotFoundError, ProcessLookupError): pass
