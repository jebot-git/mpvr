# mpvr XR playback

mpvr adds existing-SBS playback to an upstream mpv checkout. It does not synthesize
3D depth or convert ordinary video to stereo. Normal videos can be shown identically
to both eyes. The Python launcher routes processes; decoding, audio, synchronization,
GPU rendering and seeking remain in the compiled mpv player.

## Commands

```sh
mpvr movie_SBS.mkv                     # active DomeD/WayVR, otherwise normal desktop
mpv movie_HSBS.mkv                     # local compatibility command
mpvr --xr-output=wayvr --sbs-layout=full-sbs movie.mkv
mpvr --domed --sbs-layout=half-sbs movie.mkv
mpvr --xr-output=desktop --sbs-layout=full-sbs movie.mkv # squeezed SBS transport preview
mpvr --xr-output=off movie.mkv          # upstream desktop behavior
mpvr --swap-eyes movie_SBS.mkv
mpvr --xr-help
```

`--xr-output=auto` is the default. Inside a recognized DomeD session it stays with
that compositor: an inactive/older DomeD falls back to ordinary desktop viewing.
Otherwise a connected, running WiVRn session selects WayVR. Explicit `wayvr` or
`domed` fails clearly if unavailable. No runtime/session is started or restarted.
Introspection commands such as `--version` never open XR windows.

`--xr-size=2560x1440` sets the default transport resolution (width must be even).
Each eye is fitted to the entire apparent canvas, then squeezed into its half
of the transport. Full SBS and half SBS retain their intended aspect ratio with
independent, symmetric margins. A GPU MAIN shader handles fitting and eye order.
There is no Python frame loop, model inference or CPU stereo3d pixel filter.
The format filter only tags metadata for mpv's existing per-eye subtitle/OSD code.

Auto detection uses source SBS metadata first, preserving its declared per-eye
display aspect, then filename tokens: SBS/FSBS/FULL-SBS or HSBS/HALF-SBS.
RL/CROSS/CROSS-EYED filename markers reverse eye order. Explicit `--sbs-layout`
overrides detection; `--swap-eyes` toggles the detected order. A wide aspect ratio
alone does not imply SBS. Unknown/unmarked sources duplicate mono to both eyes.
Non-SBS stereo metadata (e.g. top/bottom) reports an error; an explicit layout can
override bad metadata. Layout updates on file/track changes in mpv.

Standard mpv keyboard controls (Space, arrows, q), playlists, URLs, audio and
subtitles are retained. The mouse OSC is disabled in XR because a packed stereo
surface does not have ordinary mouse geometry. Subtitles/OSD are duplicated into
both eyes. Rotation metadata is disabled to retain a horizontal eye split. XR
routing, gpu-next/OpenGL, fitting and metadata tagging are enforced after user
arguments; hardware decoding defaults to `auto-safe`, and explicit `--hwdec=...`
is preserved. Normal desktop mode forwards mpv configuration/options unchanged.
Avoid additional crop/rotate/stereo pixel filters in XR mode; these can change
the packed input after layout detection. HDR behavior depends on mpv's color
management and the XR compositor/runtime; this prototype was validated with SDR.

## Backends and ownership

WayVR launches one owned process/overlay with `left_right` and
`stereo_full_frame=false`. Encoded argument transport preserves spaces, Unicode
and URL characters despite WayVR's whitespace splitting. The supervising launcher
keeps private mpv IPC, waits for playback to exit, and cleans its own process on
exit or interruption. Set `MPVR_WAYVRCTL` for a nonstandard controller path.

DomeD requires the updated compositor from this project. Discovery validates
same-user Wayland peer PID/executable, the owned non-symlink session record,
XR socket identity and the numeric loopback integration response. It connects
the new player directly to that compositor's XR socket with app ID
`org.mpvr.Sbs`; DomeD submits left/right halves to separate OpenXR eyes. No other
window/app ID opts into stereo. It uses quad/cylinder layers already implemented
for qimgvr. DomeD also advertises `wp_viewporter`, required by current mpv.

Optional Dome-only default video associations:

```sh
python3 xr/install-domed.py
python3 xr/install-domed.py --disable  # restore tracked previous Dome defaults
```

## Build and local replacement

Use upstream mpv's dependencies plus Python 3 for the launcher. Required: FFmpeg,
libplacebo >=7.360.1, libass, LuaJIT/Lua, OpenGL/EGL, Wayland (including protocol
headers), and audio development packages. VAAPI enables Intel hardware decoding.
X11/EGL is used by isolated render tests; PipeWire is the local audio backend.

```sh
meson setup build --prefix="$HOME/.local/opt/mpvr" -Dbuildtype=release \
  -Dlibmpv=false -Dtests=true -Dlua=enabled
meson compile -C build
meson test -C build --print-errorlogs
python3 -m unittest discover -s tests/xr -p test_launcher.py
python3 xr/install.py
```

The installer copies the compiled player to `~/.local/opt/mpvr/libexec/mpvr/mpv`,
assets to `share/mpvr`, and creates `~/.local/bin/mpvr` and `~/.local/bin/mpv`.
It supplies a desktop entry and a user override of `mpv.desktop` so existing mpv
file associations use the new build. Existing files are backed up under
`~/.local/state/mpvr/backups`. The distribution `/usr/bin/mpv` and system libmpv
remain available; qimgvr's mpv plugin continues using its installed shared library.
Put `~/.local/bin` first in PATH (already configured on the development workstation).
Reopen running players to use the updated build.

The local build used privately extracted development RPMs under
`~/.cache/mpver-build/sysroot` to link against existing system runtimes. These
headers are not runtime dependencies or repository contents. Ordinary builds
use development packages installed through the distribution package manager.

## Validation

- Upstream Meson unit suite: 13 passed.
- Launcher tests: routing, inactive sessions, off/help behavior, separator handling,
  lossless encoded arguments and invalid surfaces.
- `tests/xr/test_playback.py`: real compiled gpu-next on private Xvfb/Mesa software
  rendering. Pixel checks cover full/half SBS, metadata full/half, right-first,
  mono duplication, portrait margins and duplicated subtitles. Audio decoding
  uses a null output; timing and seeking are checked.
- `tests/xr/test_domed.py`: private D-Bus/Xvfb and actual nested DomeD. Simulates
  only the XR heartbeat. Verifies XR-only placement, per-eye source pixels,
  Unicode paths, cleanup and inactive-session failure.
- `tests/xr/test_wayvr.py`: optional LIVE test, requires connected WiVRn/WayVR.
  Creates one owned muted overlay, checks stereo attributes, rendered eye pixels,
  playback progression, actual hardware decoder and cleanup. No other overlays
  are modified. Validated on Intel Arc A770 with `hwdec-current=vaapi`.

Render/integration tests need NumPy and OpenCV in their Python environment; the
installed Arc SBS interpreter provides these. Fixtures/logs/reports live under
ignored `artifacts/`. These checks validate submitted pixels/routing, not
through-headset visual perception or a sustained playback performance benchmark.

Sources: [upstream mpv](https://github.com/mpv-player/mpv),
[GPU shader interface](https://mpv.io/manual/master/#options-glsl-shaders),
[source stereo metadata](https://mpv.io/manual/master/#command-interface-video-params).
