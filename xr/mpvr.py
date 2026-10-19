#!/usr/bin/env python3
"""mpvr: native mpv playback with owned WayVR or DomeD SBS output."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent


def native_player():
    installed = HERE.parent.parent / 'libexec/mpvr/mpv'
    source = HERE.parent / 'build/mpv'
    return installed if installed.is_file() else source


def socket_pid(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        sock.connect(str(path))
        pid, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != os.getuid() or Path(f'/proc/{pid}/exe').resolve().name != 'dome-compositor':
        raise ValueError('Wayland socket is not owned by your DomeD compositor')
    return pid


def domed_status():
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/nonexistent'))
    display = os.environ.get('WAYLAND_DISPLAY', '')
    if not display:
        return None
    path = Path(display) if Path(display).is_absolute() else runtime / display
    owned = None
    try:
        pid = socket_pid(path)
        owned = dict(socket='', active=False)
        record = Path(str(path) + '.dome-session.json')
        info = record.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022 or info.st_size > 4096):
            return None
        value = json.loads(record.read_text())
        port, xr = value.get('control_port'), value.get('xr_socket')
        if (value.get('version') != 1 or value.get('compositor_pid') != pid
                or type(port) is not int or not 1 <= port <= 65535 or not isinstance(xr, str) or not xr):
            return None
        if socket_pid(Path(xr) if Path(xr).is_absolute() else runtime / xr) != pid:
            return None
        http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with http.open(f'http://127.0.0.1:{port}/api/integrations/mpvr', timeout=2) as response:
            status = json.loads(response.read(4097))
        if (status.get('version') == 1 and status.get('compositor_pid') == pid
                and status.get('xr_socket') == xr and status.get('sbs_app_id') == 'org.mpvr.Sbs'):
            return dict(socket=xr, active=status.get('xr_active') is True)
    except (OSError, ValueError, TypeError):
        pass
    return owned


def headset_connected():
    try:
        for prop in ('HeadsetConnected', 'SessionRunning'):
            r = subprocess.run(['busctl', '--user', 'get-property', 'io.github.wivrn.Server',
                '/io/github/wivrn/Server', 'io.github.wivrn.Server', prop],
                capture_output=True, text=True, timeout=2, check=True)
            if r.stdout.strip() != 'b true': return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def wayvr_controller():
    return (os.environ.get('MPVR_WAYVRCTL') or shutil.which('wivrn-pro-wayvrctl')
            or shutil.which('wayvrctl') or str(Path.home() / '.local/opt/wivrn-pro/bin/wivrn-pro-wayvrctl'))


def control(ctl, *args):
    r = subprocess.run([ctl, *args], capture_output=True, text=True, timeout=10)
    if r.returncode: raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def video_args(args, size, name, ipc, layout, swap, context='wayland'):
    # Explicit routing/rendering options follow user arguments so config cannot
    # silently redirect the XR window or reintroduce whole-frame letterboxing.
    managed = ['--vo=gpu-next', '--gpu-api=opengl', f'--gpu-context={context}',
        '--keepaspect=no', '--video-rotate=no', '--border=no',
        '--osc=no', '--blend-subtitles=no', '--idle=no', '--force-window=yes',
        f'--geometry={size}', f'--title={name}', '--wayland-app-id=org.mpvr.Sbs',
        f'--input-ipc-server={ipc}', f'--script={HERE / "mpvr.lua"}',
        f'--glsl-shaders-append={HERE / "sbs.glsl"}',
        '--vf-append=format:stereo-in=sbs2l',
        f'--script-opts-append=mpvr-layout={layout}',
        f'--script-opts-append=mpvr-swap={"yes" if swap else "no"}']
    if not any(a.startswith('--hwdec=') or a == '--no-hwdec' for a in args):
        managed.append('--hwdec=auto-safe')
    if '--' in args:
        split = args.index('--')
        return [*args[:split], *managed, *args[split:]]
    return [*args, *managed]


def connect_ipc(path, child=None, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child and child.poll() is not None:
            raise RuntimeError(f'mpv exited during startup ({child.returncode})')
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(path)); return sock
        except OSError:
            sock.close(); time.sleep(.05)
    raise RuntimeError('mpv did not open its playback IPC socket')


def encode_args(args):
    return base64.urlsafe_b64encode(json.dumps(args, ensure_ascii=False).encode()).decode()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['--_xr-child']:
        if len(argv) != 2: raise ValueError('Invalid internal launch')
        args = json.loads(base64.urlsafe_b64decode(argv[1]))
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError('Invalid playback arguments')
        os.execv(native_player(), [str(native_player()), *args])
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--xr-help', action='store_true')
    parser.add_argument('--xr-output', choices=('auto', 'wayvr', 'domed', 'desktop', 'off'), default='auto')
    parser.add_argument('--domed', action='store_true')
    parser.add_argument('--sbs-layout', choices=('auto', 'full-sbs', 'half-sbs', 'mono'), default='auto')
    parser.add_argument('--swap-eyes', action='store_true')
    parser.add_argument('--xr-size', default='2560x1440')
    opts, args = parser.parse_known_args(argv)
    if opts.xr_help:
        parser.print_help(); return 0
    if opts.domed: opts.xr_output = 'domed'
    import re
    if not re.fullmatch(r'[1-9][0-9]{1,3}x[1-9][0-9]{1,3}', opts.xr_size):
        raise ValueError('--xr-size must be WIDTHxHEIGHT (10–9999 pixels)')
    if int(opts.xr_size.split('x')[0]) % 2:
        raise ValueError('--xr-size width must be even')
    # Introspection and normal CLI help never create XR surfaces.
    option_args = args[:args.index('--')] if '--' in args else args
    info = any(a in ('--help', '-h', '--version', '-V', '--list-options', '--list-protocols', '--list-properties')
               or a.endswith('=help') for a in option_args)
    backend = opts.xr_output
    dome = domed_status() if backend in ('auto', 'domed') and not info else None
    if backend == 'auto':
        # Inside DomeD stay in its realm; never fall through into another desktop.
        if dome is not None: backend = 'domed' if dome['active'] else 'off'
        else: backend = 'wayvr' if not info and headset_connected() else 'off'
    if '--version' in option_args or '-V' in option_args:
        print('mpvr 0.1.0 (SBS XR fork of mpv)', flush=True)
    if info or not args or backend == 'off':
        os.execv(native_player(), [str(native_player()), *args])
    if backend == 'domed' and not (dome and dome['active']):
        raise RuntimeError('No active compatible DomeD XR session on the current Wayland socket')
    if backend == 'wayvr' and not headset_connected():
        raise RuntimeError('WiVRn headset/session is not connected')
    name = f'mpvr-sbs-{os.getpid()}'
    child, handle, ipc_socket = None, None, None
    ctl = wayvr_controller()
    with tempfile.TemporaryDirectory(prefix=f'mpvr-{os.getpid()}-') as directory:
        ipc = Path(directory) / 'ipc'
        context = 'x11egl' if backend == 'desktop' and not os.environ.get('WAYLAND_DISPLAY') else 'wayland'
        playback = video_args(args, opts.xr_size, name, ipc, opts.sbs_layout, opts.swap_eyes, context)
        try:
            if backend == 'wayvr':
                result = control(ctl, 'process-launch', '-n', name, '--', str(Path(__file__).resolve()),
                                 opts.xr_size, 'floating', '', '--_xr-child ' + encode_args(playback))
                payload = json.loads(result); handle = payload.get('handle', payload)
            else:
                env = os.environ.copy()
                if backend == 'domed':
                    env['WAYLAND_DISPLAY'] = dome['socket']; env.pop('WAYLAND_SOCKET', None)
                child = subprocess.Popen([str(native_player()), *playback], env=env)
            ipc_socket = connect_ipc(ipc, child)
            if backend == 'wayvr':
                deadline = time.monotonic() + 10
                while name not in control(ctl, 'window-list').splitlines():
                    if time.monotonic() > deadline: raise RuntimeError('WayVR window did not appear')
                    time.sleep(.1)
                control(ctl, 'window-attrib', name, 'set', 'stereo', 'left_right')
                control(ctl, 'window-attrib', name, 'set', 'stereo_full_frame', 'false')
                control(ctl, 'window-set-visible', name, '1')
            print(f'mpvr: {backend} SBS output ({name})', file=sys.stderr)
            while ipc_socket.recv(65536): pass
            return child.wait(timeout=5) if child else 0
        finally:
            if ipc_socket:
                try: ipc_socket.sendall(b'{"command":["quit"]}\n')
                except OSError: pass
                ipc_socket.close()
            if child and child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait()
            if handle is not None:
                try: control(ctl, 'process-terminate', json.dumps(handle))
                except (OSError, RuntimeError, subprocess.SubprocessError): pass


if __name__ == '__main__':
    def interrupted(signum, frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'mpvr: {exc}', file=sys.stderr); sys.exit(2)
