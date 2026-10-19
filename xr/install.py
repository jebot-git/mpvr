#!/usr/bin/env python3
"""Install the tested mpvr build and replace the current user's mpv launcher."""
import datetime
import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PREFIX = Path.home() / '.local/opt/mpvr'
BACKUP = Path.home() / '.local/state/mpvr/backups' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')


def backup(path):
    if path.exists() or path.is_symlink():
        target = BACKUP / path.relative_to(Path.home())
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink(): target.symlink_to(os.readlink(path))
        else: shutil.copy2(path, target)


def main():
    binary = ROOT / 'build/mpv'
    subprocess.run([str(binary), '--version'], check=True)
    native = PREFIX / 'libexec/mpvr/mpv'
    native.parent.mkdir(parents=True, exist_ok=True)
    backup(native)
    shutil.copy2(binary, native)
    assets = PREFIX / 'share/mpvr'
    assets.mkdir(parents=True, exist_ok=True)
    for name in ('mpvr.py', 'mpvr.lua', 'sbs.glsl'):
        backup(assets / name)
        shutil.copy2(ROOT / 'xr' / name, assets / name)
    (assets / 'mpvr.py').chmod(0o755)
    launchers = [PREFIX / 'bin/mpvr', Path.home() / '.local/bin/mpvr', Path.home() / '.local/bin/mpv']
    for launcher in launchers:
        backup(launcher)
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.unlink(missing_ok=True)
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(assets / 'mpvr.py')) + ' "$@"\n')
        launcher.chmod(0o755)
    applications = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    exe = str(Path.home() / '.local/bin/mpvr').replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    entry = ('[Desktop Entry]\nType=Application\nName=mpvr\nComment=Play videos with optional SBS XR output\n'
             f'Exec="{exe}" -- %U\nIcon=mpv\nTerminal=false\nCategories=AudioVideo;Video;Player;\n'
             'MimeType=video/mp4;video/x-matroska;video/webm;video/quicktime;video/x-msvideo;video/mpeg;\n')
    for name in ('mpvr.desktop', 'mpv.desktop'):
        path = applications / name; backup(path)
        path.write_text(entry + ('NoDisplay=true\n' if name == 'mpv.desktop' else ''))
        subprocess.run(['desktop-file-validate', str(path)], check=True)
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(applications)], check=True)
    print(f'Installed mpvr; local mpv now uses {native}\nBackups: {BACKUP}')
    print('The distribution binary remains available at /usr/bin/mpv.')


if __name__ == '__main__': main()
