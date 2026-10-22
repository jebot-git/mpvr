#!/usr/bin/env python3
"""mpvr: native mpv playback with owned WayVR or DomeD SBS output."""
import argparse
import base64
import json
import os
import re
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
import urllib.parse

HERE = Path(__file__).resolve().parent


def bdf_appearance_args(args):
    """Resolve native BDF text before starting libass in the player process."""
    candidates = [os.environ.get('DOME_BDF_FONT_TOOL'), shutil.which('dome-bdf-font'),
                  HERE.parents[1] / 'bin/dome-bdf-font',
                  HERE.parents[1] / 'dome-compositor/target/debug/dome-bdf-font',
                  HERE.parents[2] / 'dome-compositor/target/debug/dome-bdf-font']
    helper = next((Path(path) for path in candidates if path and Path(path).is_file()), None)
    if helper is None:
        raise RuntimeError('mpvr requires dome-bdf-font and BDF-enabled libass')
    chosen = json.loads(subprocess.check_output([str(helper)], text=True, timeout=30))
    os.environ['FONTCONFIG_FILE'] = chosen['fontconfig']
    os.environ['DE_XR_FONTCONFIG_BASE'] = chosen.get('fontconfig_base', '/etc/fonts/fonts.conf')
    family, pixels = chosen.get('media_family', chosen['family']), chosen['pixels']
    managed = []
    for name, value in [('osd-font', family), ('sub-font', family), ('osd-font-size', pixels), ('sub-font-size', pixels)]:
        if not any(arg.startswith('--' + name + '=') for arg in args):
            managed.append('--' + name + '=' + str(value))
    index = args.index('--') if '--' in args else len(args)
    return args[:index] + managed + args[index:]


def native_player():
    if os.environ.get('MPVR_NATIVE_PLAYER'):
        return Path(os.environ['MPVR_NATIVE_PLAYER'])
    installed = HERE.parent.parent / 'libexec/mpvr/mpv'
    source = HERE.parent / 'build/mpv'
    return installed if installed.is_file() else source


def socket_pid(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        sock.connect(str(path))
        pid, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    # A package update can unlink the executable of a still-running session.
    # readlink preserves Linux's deletion marker; resolve treats it as a path.
    executable = os.readlink(f'/proc/{pid}/exe').removesuffix(' (deleted)')
    if uid != os.getuid() or Path(executable).name != 'dome-compositor':
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
            return owned
        value = json.loads(record.read_text())
        port, xr = value.get('control_port'), value.get('xr_socket')
        if (value.get('version') != 1 or value.get('compositor_pid') != pid
                or type(port) is not int or not 1 <= port <= 65535 or not isinstance(xr, str) or not xr):
            return owned
        if socket_pid(Path(xr) if Path(xr).is_absolute() else runtime / xr) != pid:
            return owned
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


def stereo_metadata(value):
    """Return supported packed stereo, explicit mono, unsupported stereo, or no evidence."""
    def normalize(text):
        return str(text).lower().replace('-', '_').replace(' ', '_')
    for stream in value.get('streams', []):
        tags = {str(k).lower(): v for k, v in stream.get('tags', {}).items()}
        if 'stereo_mode' in tags:
            mode = normalize(tags['stereo_mode'])
            if mode in ('left_right', 'right_left', 'side_by_side', 'sbs2l', 'sbs2r', '1', '11'):
                return 'sbs'
            if mode in ('top_bottom', 'bottom_top', 'above_below', 'tb', 'ab2l', 'ab2r', '2', '3'): return 'tb'
            if mode in ('mono', '2d', '0'): return 'mono'
            return 'unsupported'
    for item in [*value.get('streams', []), *value.get('frames', [])]:
        for data in item.get('side_data_list', []):
            if normalize(data.get('side_data_type')) != 'stereo_3d': continue
            mode = normalize(data.get('type', ''))
            if mode in ('side_by_side', 'sidebyside', 'left_right', 'right_left'): return 'sbs'
            if mode in ('top_bottom', 'bottom_top', 'top_and_bottom'): return 'tb'
            if mode in ('2d', 'mono'): return 'mono'
            return 'unsupported'
    return None


def recognize_video(source, requested='auto'):
    if requested != 'auto':
        return requested in ('full-sbs', 'half-sbs', 'full-tb', 'half-tb'), 'manual ' + requested
    parsed = urllib.parse.urlsplit(source)
    path = urllib.parse.unquote(parsed.path)
    if Path(path).suffix.lower() in ('.m3u', '.m3u8', '.pls', '.xspf', '.mpls'):
        return False, 'playlist/manifest requires explicit SBS layout'
    probe_source = (urllib.request.url2pathname(parsed.path)
                    if parsed.scheme == 'file' and parsed.netloc in ('', 'localhost') else source)
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-read_intervals', '%+#4', '-show_streams', '-show_frames',
            '-show_entries', 'stream=index,width,height:stream_tags=stereo_mode:stream_side_data:frame_side_data',
            '-of', 'json', probe_source], capture_output=True, text=True, timeout=4, check=True)
        metadata = stereo_metadata(json.loads(result.stdout))
        if metadata is not None:
            return metadata in ('sbs', 'tb'), 'source metadata: ' + metadata
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    stem = Path(path).stem.upper()
    if re.search(r'(?<![A-Z0-9])(?:H[-_. ]?SBS|HALF[-_. ]?SBS|F[-_. ]?SBS|FULL[-_. ]?SBS|SBS)(?![A-Z0-9])', stem):
        return True, 'SBS filename marker'
    if re.search(r'(?<![A-Z0-9])(?:H[-_. ]?TB|HALF[-_. ]?TB|F[-_. ]?TB|FULL[-_. ]?TB|TB|TAB|OVER[-_. ]?UNDER)(?![A-Z0-9])', stem):
        return True, 'top/bottom filename marker'
    return False, 'no supported stereo evidence'


def only_stereo_sources(args, requested):
    # An opaque playlist/script may add 2D files later. Keep uncertain launch
    # requests on the desktop instead of exposing packed mono to the headset.
    if requested == 'mono': return False
    if requested in ('full-sbs', 'half-sbs', 'full-tb', 'half-tb'): return True
    before = args[:args.index('--')] if '--' in args else args
    for arg in before:
        if arg.startswith(('--playlist', '--autocreate-playlist', '--{')): return False
    sources = [arg for arg in before if not arg.startswith('-')]
    if '--' in args: sources += args[args.index('--') + 1:]
    return bool(sources) and all(recognize_video(source, requested)[0] for source in sources)


def video_args(args, size, name, ipc, layout, swap, context='wayland', stereo_only=False):
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
        f'--script-opts-append=mpvr-swap={"yes" if swap else "no"}',
        f'--script-opts-append=mpvr-stereo_only={"yes" if stereo_only else "no"}']
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


def monitor_playback(sock):
    sock.sendall(b'{"command":["observe_property",991,"user-data/mpvr"]}\n')
    with sock.makefile('rb') as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            data = event.get('data')
            if (event.get('event') == 'property-change' and event.get('id') == 991
                    and isinstance(data, dict) and data.get('desktop') is True
                    and isinstance(data.get('path'), str)):
                position = data.get('start', 0)
                if not isinstance(position, (int, float)) or position < 0: position = 0
                state_args = []
                if type(data.get('mute')) is bool:
                    state_args.append('--mute=' + ('yes' if data['mute'] else 'no'))
                for key in ('volume', 'speed'):
                    if isinstance(data.get(key), (int, float)):
                        state_args.append(f'--{key}={data[key]}')
                child = subprocess.Popen([str(native_player()), *state_args, f'--start={position}', '--', data['path']])
                print(f'mpvr: 2D content moved to desktop (PID {child.pid})', file=sys.stderr)
                return


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
    parser.add_argument('--sbs-layout', choices=('auto', 'full-sbs', 'half-sbs', 'full-tb', 'half-tb', 'mono'), default='auto')
    parser.add_argument('--swap-eyes', action='store_true')
    parser.add_argument('--xr-size', default='2560x1440')
    opts, args = parser.parse_known_args(argv)
    if opts.xr_help:
        parser.print_help(); return 0
    if opts.domed: opts.xr_output = 'domed'
    if not re.fullmatch(r'[1-9][0-9]{1,3}x[1-9][0-9]{1,3}', opts.xr_size):
        raise ValueError('--xr-size must be WIDTHxHEIGHT (10–9999 pixels)')
    if int(opts.xr_size.split('x')[0]) % 2:
        raise ValueError('--xr-size width must be even')
    # Introspection and normal CLI help never create XR surfaces.
    option_args = args[:args.index('--')] if '--' in args else args
    info = any(a in ('--help', '-h', '--version', '-V', '--list-options', '--list-protocols', '--list-properties')
               or a.endswith('=help') for a in option_args)
    sources = [a for a in option_args if not a.startswith('-')]
    if '--' in args: sources += args[args.index('--') + 1:]
    gui_idle = ('--player-operation-mode=pseudo-gui' in option_args and not sources
                and not any(a.startswith('--playlist=') for a in option_args))
    # Opening the application menu without a file should create the normal
    # idle GUI, including inside an active DomeD session.
    if not info:
        args = bdf_appearance_args(args)
    backend = 'off' if gui_idle else opts.xr_output
    dome = domed_status() if backend in ('auto', 'domed', 'wayvr') and not info else None
    if backend == 'wayvr' and dome is not None:
        backend = 'domed' if dome['active'] else 'off'
    if backend == 'auto':
        # Inside DomeD stay in its realm; never fall through into another desktop.
        if dome is not None: backend = 'domed' if dome['active'] else 'off'
        else: backend = 'wayvr' if not info and headset_connected() else 'off'
    if backend == 'wayvr' and not only_stereo_sources(args, opts.sbs_layout):
        print('mpvr: 2D or unverified input; using desktop playback', file=sys.stderr)
        backend = 'off'
    if '--version' in option_args or '-V' in option_args:
        print('mpvr 0.1.2 (SBS XR fork of mpv)', flush=True)
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
        playback = video_args(args, opts.xr_size, name, ipc, opts.sbs_layout, opts.swap_eyes, context, stereo_only=backend == 'wayvr')
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
            monitor_playback(ipc_socket)
            if child and child.poll() is None:
                try: ipc_socket.sendall(b'{"command":["quit"]}\n')
                except OSError: pass
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
