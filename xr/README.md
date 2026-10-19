# mpvr XR playback

mpvr adds existing-SBS playback to an upstream mpv checkout. It does not synthesize
3D depth or convert ordinary video to stereo. Outside DomeD, ordinary videos stay
on the desktop. DomeD and the explicit transport preview can duplicate mono to both eyes. The Python launcher routes processes; decoding, audio, synchronization,
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
Otherwise a connected, running WiVRn session selects WayVR only for recognized
SBS input. 2D and unverified files use normal desktop playback, including when
`--xr-output=wayvr` was requested without an explicit SBS layout. Explicit XR
requests for recognized stereo fail clearly if the backend is unavailable. No runtime/session is started or restarted.
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
alone does not imply SBS, and a bare 3D filename label is insufficient.
Before WayVR launch, ffprobe checks stream tags and the first four video packets
for Stereo 3D side data (four-second timeout). Explicit mono/unsupported metadata
overrides filename hints. If probing is unavailable, only explicit SBS filename
markers or a manual full/half layout qualify. Mixed or opaque playlists/manifests
stay on the desktop unless explicitly identified as SBS. Inside DomeD, unknown
sources duplicate mono to both eyes. Non-SBS stereo metadata (e.g. top/bottom)
uses desktop playback outside DomeD; DomeD reports an unsupported-layout error.
An explicit layout can override bad metadata. Layout updates on file/track changes in mpv. A WayVR runtime guard blacks and
pauses unrecognized/2D content loaded later (including IPC/playlist changes),
then hands it to a native desktop player and closes only its own XR overlay.
The handoff preserves playback position, volume, mute and speed. It does not
resume the remaining XR playlist after that handoff.

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

## Player UI

The local build enables the mpv player and LuaJIT (`cplayer=true`, `lua=luajit`),
with libass rendering for the built-in on-screen controller, context menu,
selection dialogs, console and OSD. Desktop playback enables the standard
mouse controls. Right-click opens the context menu; Ctrl+P opens the menu,
Space toggles pause, arrows seek, and the backtick key opens the console.

The application-menu and file-association entries use upstream's
`--player-operation-mode=pseudo-gui`. Opening the application with no file keeps
an idle desktop window available for dropping media. This idle launch stays on
the desktop even when DomeD/XR is active. Command-line invocation retains normal
mpv behavior; use `mpvr --player-operation-mode=pseudo-gui` for an idle window.

SBS output retains keyboard controls and duplicates console/OSD to both eyes.
The ordinary mouse OSC remains disabled on the packed XR surface because it
expects desktop pointer coordinates. It is included in the executable and
available for desktop playback.

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
libplacebo >=7.360.1, libass, LuaJIT/Lua, ffprobe for metadata recognition, OpenGL/EGL, Wayland (including protocol
headers), and audio development packages. VAAPI enables Intel hardware decoding.
X11/EGL is used by isolated render tests; PipeWire is the local audio backend.

```sh
meson setup build --prefix="$HOME/.local/opt/mpvr" -Dbuildtype=release \
  -Dcplayer=true -Dlibmpv=false -Dtests=true -Dlua=enabled
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
- Launcher tests: 15 passed; metadata/filename precedence, wide 2D input, mixed
  playlists, runtime handoff, routing, inactive sessions, off/help behavior,
  separator handling, lossless encoded arguments and invalid surfaces.
- `tests/xr/test_ui.py`: verifies the installed executable on private Xvfb:
  idle GUI startup, rendered OSC, mouse play/pause, context menu, console, and
  keyboard controls with console rendering in both SBS eyes.
- `tests/xr/test_playback.py`: real compiled gpu-next on private Xvfb/Mesa software
  rendering. Pixel checks cover full/half SBS, metadata full/half, right-first,
  mono duplication, portrait margins and duplicated subtitles. Audio decoding
  uses a null output; timing and seeking are checked.
- `tests/xr/test_routing.py`: optional LIVE non-DomeD test with a connected
  headset. Verifies that a real 2D file opens as a normal desktop player,
  an unmarked metadata-only SBS file opens in WayVR, and loading 2D into that
  running player hands off to the desktop and cleans up its XR window.
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
