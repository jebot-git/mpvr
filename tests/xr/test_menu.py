#!/usr/bin/env python3
"""Real Wayland DBusMenu export on a private DomeD and session bus (Python GI).

Run with /usr/bin/python3 tests/xr/test_menu.py. No live session is modified.
"""
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

if os.environ.get('MPVR_MENU_TEST_BUS') != '1':
    os.execvpe('dbus-run-session', ['dbus-run-session', '--', sys.executable, *sys.argv],
               dict(os.environ, MPVR_MENU_TEST_BUS='1'))
from gi.repository import Gio, GLib
from Xlib import X, display as xdisplay
from Xlib.ext import xtest

ROOT = Path(__file__).resolve().parents[2]
DOME = Path(os.environ.get('MPVR_DOME_SOURCE', str(Path.home() / 'DomeD')))
ART = ROOT / 'artifacts/menu'
ART.mkdir(parents=True, exist_ok=True)
children, logs = [], []
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def call(service, path, interface, method, signature, args):
    return bus.call_sync(service, path, interface, method, GLib.Variant(signature, args),
                         None, Gio.DBusCallFlags.NONE, 3000, None).unpack()


def wait(check, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError, KeyError, GLib.Error):
            pass
        time.sleep(.05)
    raise AssertionError('Timed out; inspect artifacts/menu')


def start(args, name, env, **kwargs):
    log = (ART / (name + '.log')).open('w')
    logs.append(log)
    child = subprocess.Popen(list(map(str, args)), env=env, stdout=log, stderr=log, **kwargs)
    children.append(child)
    return child


def owner(pid):
    names = call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                 'ListNames', '()', ())[0]
    for name in names:
        if name.startswith(':'):
            found = call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                         'GetConnectionUnixProcessID', '(s)', (name,))[0]
            if found == pid:
                return name


def flatten(node):
    yield node
    for child in node[2]:
        yield from flatten(child)


try:
    with tempfile.TemporaryDirectory(prefix='mpvr-menu-') as directory:
        runtime = Path(directory)
        runtime.chmod(0o700)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, value=None):
            req = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                data=None if value is None else json.dumps(value).encode(),
                headers={'Content-Type': 'application/json'})
            with http.open(req, timeout=3) as response:
                return json.load(response)

        rd, wr = os.pipe()
        xvfb = shutil.which('Xvfb') or str(DOME / '.local-deps/root/usr/bin/Xvfb')
        start([xvfb, '-displayfd', wr, '-screen', '0', '1280x720x24', '-nolisten', 'tcp'],
              'xvfb', os.environ.copy(), pass_fds=(wr,))
        os.close(wr)
        with os.fdopen(rd) as pipe:
            display = ':' + pipe.readline().strip()
        env = dict(os.environ, DISPLAY=display, XDG_RUNTIME_DIR=directory,
            XDG_CONFIG_HOME=str(runtime / 'config'), XDG_DATA_HOME=str(runtime / 'data'),
            DOME_PREVIEW_PORT=str(port), DOME_START_XR='0', LIBGL_ALWAYS_SOFTWARE='1',
            XR_RUNTIME_JSON=str(runtime / 'no-runtime.json'), RUST_LOG='error')
        env.pop('WAYLAND_DISPLAY', None)
        env.pop('WAYLAND_SOCKET', None)
        start([os.environ.get('MPVR_DOME_BINARY', str(DOME / 'target/debug/dome-compositor')),
               '--backend', 'nested'], 'compositor', env)
        wait(lambda: request('/api/state'))
        records = wait(lambda: list(runtime.glob('*.dome-session.json')))
        record = next(p for p in records if json.loads(p.read_text())['realm'] == 'desktop')
        env['WAYLAND_DISPLAY'] = str(record).removesuffix('.dome-session.json')
        ipc_path = runtime / 'mpv.sock'
        player = start([os.environ.get('MPVR_NATIVE_PLAYER', str(ROOT / 'build/mpv')),
                        '--no-config', '--idle=yes', '--force-window=yes',
                        '--vo=gpu-next', '--gpu-api=opengl', '--gpu-context=wayland', '--gpu-sw=yes',
                        '--ao=null', f'--input-ipc-server={ipc_path}'], 'player', env)
        wait(ipc_path.exists)
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(5)
        sock.connect(str(ipc_path))
        with sock, sock.makefile('rwb', buffering=0) as stream:
            def ipc(command):
                stream.write(json.dumps({'command': command}).encode() + b'\n')
                while True:
                    reply = json.loads(stream.readline())
                    if 'error' in reply:
                        assert reply['error'] == 'success', reply
                        return reply.get('data')

            service = wait(lambda: owner(player.pid))

            def menu(method, sig='()', args=()):
                return call(service, '/org/mpvr/Menu', 'com.canonical.dbusmenu', method, sig, args)

            def layout():
                return menu('GetLayout', '(iias)', (0, -1, []))[1]

            native = wait(lambda: layout() if len(layout()[2]) > 3 else None)
            assert any('Fullscreen' in n[1].get('label', '') for n in flatten(native))
            ipc(['set_property', 'mute', True])
            wait(lambda: any(n[1].get('label') == '_Mute' and n[1].get('toggle-state') == 1
                             for n in flatten(layout())))
            ipc(['set_property', 'mute', False])
            wait(lambda: any(n[1].get('label') == '_Mute' and 'toggle-state' not in n[1]
                             for n in flatten(layout())))
            print('PASS: native live menu export', flush=True)

            # A controlled model exercises types, filtering, depth, nested commands,
            # disabled/hidden actions, shortcuts, and stale-ID rejection.
            custom = [dict(type='submenu', title='&Fixture', submenu=[
                dict(title='&Toggle mute', cmd='cycle mute', shortcut='Ctrl+m', state=['checked']),
                dict(title='Disabled', cmd='set volume 1', state=['disabled']),
                dict(title='Hidden', cmd='set volume 2', state=['hidden']),
                dict(type='separator'),
                dict(type='submenu', title='Nested', submenu=[dict(title='Pause', cmd='cycle pause')])])]

            def reset_fixture():
                # select.lua repopulates menu-data after observed playback changes.
                time.sleep(.3)
                ipc(['set_property', 'menu-data', custom])
                return wait(lambda: next((n for n in layout()[2]
                            if n[1].get('label') == '_Fixture'), None))

            fixture = reset_fixture()
            toggle, disabled, hidden, separator, nested = fixture[2]
            assert toggle[1]['toggle-state'] == 1 and toggle[1]['shortcut'] == [['Control', 'm']]
            assert not disabled[1]['enabled'] and not hidden[1]['visible']
            assert separator[1]['type'] == 'separator'
            assert menu('GetLayout', '(iias)', (fixture[0], 0, ['label']))[1] == (fixture[0], {'label': '_Fixture'}, [])
            assert menu('GetProperty', '(is)', (toggle[0], 'label')) == ('_Toggle mute',)
            assert menu('GetGroupProperties', '(aias)', ([toggle[0]], ['label']))[0] == [(toggle[0], {'label': '_Toggle mute'})]
            assert menu('AboutToShow', '(i)', (fixture[0],)) == (False,)
            assert menu('AboutToShowGroup', '(ai)', ([fixture[0], -1],)) == ([], [-1])

            # Exercise the compositor's actual menu, proving the surface address
            # is consumed. File and Edit precede Fixture at x=332 in DomeD.
            xd = xdisplay.Display(display)
            host = wait(lambda: next((w for w in xd.screen().root.query_tree().children
                        if str(w.get_wm_name() or '').startswith('Dome')), None))
            host.set_input_focus(X.RevertToParent, X.CurrentTime)
            xd.sync()

            def pointer_click(x, y):
                translated = xd.screen().root.translate_coords(host, 0, 0)
                xtest.fake_input(xd, X.MotionNotify, x=translated.x + x, y=translated.y + y)
                xd.sync()
                for event in (X.ButtonPress, X.ButtonRelease):
                    xtest.fake_input(xd, event, 1)
                    xd.sync()
                    time.sleep(.1)

            before = ipc(['get_property', 'mute'])
            time.sleep(1)
            pointer_click(350, 18)
            time.sleep(.5)
            pointer_click(360, 54)
            wait(lambda: ipc(['get_property', 'mute']) != before)
            xd.close()
            print('PASS: DomeD menu mouse activation through per-surface association', flush=True)
            toggle, disabled, hidden, separator, nested = reset_fixture()[2]

            def click(item):
                menu('Event', '(isvu)', (item[0], 'clicked', GLib.Variant('i', 0), 0))

            before = ipc(['get_property', 'mute'])
            volume = ipc(['get_property', 'volume'])
            click(disabled)
            click(hidden)
            time.sleep(.1)
            assert ipc(['get_property', 'volume']) == volume
            before = ipc(['get_property', 'pause'])
            click(nested[2][0])
            wait(lambda: ipc(['get_property', 'pause']) != before)
            toggle = reset_fixture()[2][0]
            before = ipc(['get_property', 'mute'])
            click(toggle)
            wait(lambda: ipc(['get_property', 'mute']) != before)
            reset_fixture()
            assert menu('EventGroup', '(a(isvu))', ([(-1, 'clicked', GLib.Variant('i', 0), 0)],)) == ([-1],)
            ipc(['set_property', 'menu-data', []])
            wait(lambda: not layout()[2])
            try:
                click(toggle)
                raise AssertionError('Stale ID accepted')
            except GLib.Error:
                pass
            print('PASS: nested activation, checks, shortcuts, disabled/hidden state, protocol methods and stale IDs', flush=True)
            ipc(['quit'])
            assert player.wait(timeout=8) == 0
            wait(lambda: not owner(player.pid))
            print('PASS: clean shutdown and exporter disconnect', flush=True)
finally:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    for log in logs:
        log.close()
