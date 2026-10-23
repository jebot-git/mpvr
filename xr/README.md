# mpvr XR playback

mpvr adds existing packed-stereo playback to an upstream mpv checkout. It does not synthesize
3D depth or convert ordinary video to stereo. Outside DomeD, ordinary videos stay
on the desktop. DomeD and the explicit transport preview can duplicate mono to both eyes. The Python launcher routes processes; decoding, audio, synchronization,
GPU rendering and seeking remain in the compiled mpv player. This frontend uses
mpv/Lua rather than FLTK or Qt; `DE_XR_TOOLKIT` has no effect.

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
sources duplicate mono to both eyes. Top/bottom stereo metadata is repacked to the same SBS output on the GPU. Other
stereo layouts use desktop playback outside DomeD; DomeD reports an unsupported-layout error.
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
an idle desktop window available for dropping media. After the playlist finishes,
the player exits, including when started from the application menu. This idle
launch stays on the desktop even when DomeD/XR is active. Command-line invocation retains normal
mpv behavior; use `mpvr --player-operation-mode=pseudo-gui` for an idle window.

SBS output retains keyboard controls and duplicates console/OSD to both eyes.
The ordinary mouse OSC remains disabled on the packed XR surface because it
expects desktop pointer coordinates. It is included in the executable and
available for desktop playback.

On Wayland, builds with GIO (`gio-2.0 >= 2.58`) export the same live `menu-data`
model through `com.canonical.dbusmenu`, associated with the actual player surface
using KDE's `org_kde_kwin_appmenu_manager` protocol. DomeD's universal menu can
therefore invoke playback, track, subtitle, audio and window commands in desktop
and XR windows. Custom `menu.conf` definitions, nested menus, shortcuts, hidden
and disabled items, and checked states follow mpv's existing model. Top-level
context-menu actions are grouped under **Player**; existing submenus retain
their names. DomeD supplies its standard File/Edit/Window controls alongside
the application's exports.

Each window has its own D-Bus connection and endpoint. Menu changes publish
`LayoutUpdated`; removed item IDs are never reused during that window's lifetime.
Commands enter mpv's normal input queue. D-Bus calls run on a separate thread,
and closing the window removes its export. Missing GIO, the AppMenu protocol or
a session bus leaves the existing context menu available. Disabling the built-in
select script's `populate_menu_data` option requires another script to supply
`menu-data` for an application menu.

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
Discovery accepts Linux's ` (deleted)` executable marker so updating DomeD
does not prevent playback in an already-running session.

Optional Dome-only default video associations:

```sh
python3 xr/install-domed.py
python3 xr/install-domed.py --disable  # restore tracked previous Dome defaults
```

## Build and local replacement

Use upstream mpv's dependencies plus Python 3 for the launcher. Required: FFmpeg,
libplacebo >=7.360.1, libass, LuaJIT/Lua, ffprobe for metadata recognition, OpenGL/EGL, Wayland (including protocol
headers), and audio development packages. VAAPI enables Intel hardware decoding.
GIO development headers enable the optional Wayland universal-menu exporter;
the KDE AppMenu protocol XML is included in this repository.
X11/EGL is used by isolated render tests; PipeWire is the local audio backend.

```sh
meson setup build --prefix="$HOME/.local/opt/mpvr" -Dbuildtype=release \
  -Dcplayer=true -Dlibmpv=false -Dtests=true -Dlua=enabled
meson compile -C build
meson test -C build --print-errorlogs
python3 -m unittest discover -s tests/xr -p test_launcher.py
python3 xr/install.py --build-dir build
install -Dm644 xr/man/mpvr.1 "$HOME/.local/share/man/man1/mpvr.1"
```

Build and test as your regular user. `xr/install.py` is a per-user installer,
not a staging/system installer; it changes launchers and desktop entries. The
manual is installed explicitly in the example above. For an isolated shared
deployment, review the suite's staged artifacts before privileged installation.

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

The counts below describe earlier validation, not a promise about a new runtime
or headset. Run the relevant suites against your chosen executable and setup.

- Upstream Meson unit suite: 13 passed.
- Launcher tests: 15 passed; metadata/filename precedence, wide 2D input, mixed
  playlists, runtime handoff, routing, inactive sessions, off/help behavior,
  separator handling, lossless encoded arguments and invalid surfaces.
- `tests/xr/test_ui.py`: verifies the installed executable on private Xvfb:
  idle GUI startup, rendered OSC, mouse play/pause, context menu, console, and
  keyboard controls with console rendering in both SBS eyes.
- `/usr/bin/python3 tests/xr/test_menu.py`: private D-Bus/Xvfb and actual nested
  DomeD; verifies the live native menu, mute-state updates, compositor menu-bar
  mouse activation, nested commands, disabled/hidden items, shortcuts, protocol
  queries, stale-ID rejection and exporter cleanup. Requires Python GI and Xlib.
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
  Unicode paths, discovery after replacing the running compositor executable,
  cleanup and inactive-session failure.
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

## FLTube and top/bottom sources

`--sbs-layout` also accepts `full-tb` (two unsqueezed eyes stacked vertically)
and `half-tb` (each eye stored at half height). TB/FTB/FULL-TB/TAB/OVER-UNDER
and HTB/HALF-TB filename markers are recognized. Top/bottom source metadata
preserves its per-eye aspect and inverted eye order. The output remains SBS
for DomeD and WayVR, so no compositor changes are required. Eye fitting,
repacking and seam clamping run on the GPU; decoding and synchronization stay
in mpv. Unsupported checkerboard, interleaved, anaglyph and spherical VR180/360
projections are not converted by this flat-screen stereo renderer.

Inside DomeD, use FLTube's **XR video** menu or pass the original URL directly:

```sh
mpvr --sbs-layout=half-sbs 'https://www.youtube.com/watch?v=VIDEO_ID'
mpvr --domed --sbs-layout=full-tb 'https://www.youtube.com/watch?v=VIDEO_ID'
```

The built-in yt-dlp hook resolves adaptive video and audio and preserves seeking.
Automatic XR layout selection reads the resolved title's explicit packing markers
and available `stereo_mode` metadata from the selected video format. A bare “3D”
title does not imply a layout. Select the layout manually when the uploader did
not label it reliably. Outside DomeD, opaque webpage URLs need an explicit layout
to route to WayVR; the launcher does not run a second yt-dlp extraction.

Install a verified optimized build using
`python3 xr/install.py --build-dir build-fltk-release`. The installer retains
backups of existing per-user launchers/assets. This does not alter system files.

Extended validation: 21 rendered cases include full/half top/bottom, source
metadata, reversed eyes, subtitles and HTTP playback through the real yt-dlp
hook with a controlled extractor. Use `MPVR_NATIVE_PLAYER` to select the native
executable for the render suite. The separate nested DomeD test verifies XR
placement and eye delivery without restarting a live session.

## Dome desktop renderer default (2026-10-23)

Desktop windows on a validated same-user Dome Wayland socket default to OpenGL
EGL. Automatic Vulkan selection was reproduced exiting at the first frame with
`create_immed failed ... invalid wl_buffer`; the same H.264 clip completed with
EGL. This does not enable XR or change another compositor's defaults. Explicit
`--gpu-api`, `--gpu-context` and non-GPU `--vo` options remain authoritative, and
inserted options precede a filename separator. The native release player and XR
rendering pipeline are unchanged. Launcher routing has 20 passing isolated tests;
a local hardware Wayland playback check and the native UI suite validate playback.
