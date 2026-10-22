# mpvr

Native mpv fork with GPU SBS and top/bottom video playback in active WayVR/WiVRn and DomeD XR
sessions. Based on upstream mpv revision
`6a262d11f63733d37e3ee6d5fafcb1be1ef83952` (2026-10-18), the latest upstream
checkout when this fork was created. Upstream history, authors and licenses are
preserved. See [upstream README](README_UPSTREAM.md).

```sh
mpvr movie_SBS.mkv
mpvr --xr-output=wayvr --sbs-layout=full-sbs movie.mkv
mpvr --domed --sbs-layout=half-sbs movie.mkv
mpvr --xr-output=off movie.mkv
```

Installations that include the compatibility launcher also expose this build as
`mpv`; the distribution binary can coexist at `/usr/bin/mpv`. XR output fits each eye on the
GPU, retains mpv playback/audio/seek controls, and duplicates subtitles. Existing
SBS/top-bottom metadata and filename markers are recognized; explicit layout and eye-swap
options handle ambiguous files. Outside DomeD, only recognized SBS videos open
in XR; 2D/unverified videos stay on the desktop. DomeD can duplicate mono into
both eyes. A runtime guard hands later 2D content back to the desktop.
No depth synthesis or video conversion is performed.

The build includes mpv’s LuaJIT on-screen controls, context menu and console.
Wayland builds with GIO also export the live player menu to DomeD's universal
menu bar using KDE AppMenu and DBusMenu, including custom menus and track choices.
The application-menu entry opens an idle desktop GUI when no file is selected,
including inside DomeD/XR. After the playlist finishes, the player exits.

[Build, installation, UI, backend options and validation](xr/README.md).

mpvr keeps mpv's native player and Lua UI; it does not have FLTK/Qt frontend
selection or a `DE_XR_TOOLKIT` build option. Its toolkit-independent renderer
uses the same DomeD session and application-menu protocols as other de-xr apps.

The native Wayland change defers zero-sized viewport requests during startup,
fixing a protocol error reproduced on the live WayVR compositor. DomeD's companion
changes add mpvr stereo identity/discovery and the required viewporter protocol.

Upstream [GPL](LICENSE.GPL), [LGPL](LICENSE.LGPL) and [copyright notices](Copyright)
remain in force. New XR integration files are provided under GPL-2.0-or-later.

## Installed layout

The shared DomeD deployment places the launcher at `/usr/local/bin/mpvr`, its
Python/Lua/shader resources at `/usr/local/share/mpvr`, and the native player at
`/usr/local/libexec/mpvr/mpv`. The source helper in `xr/install.py` instead creates
a per-user installation; see the build guide before choosing either layout.
`mpvr --xr-help` lists XR routing options; `mpvr --help` lists mpv options.
The per-user helper does not install a manual: stage `xr/man/mpvr.1` separately
under the selected prefix's `share/man/man1` directory.

## Command manuals and related projects

Manual: [mpvr.1](xr/man/mpvr.1). Once the manual is installed, use `man mpvr`.

Part of the [de-xr desktop](https://github.com/topics/de-xr):
[DomeD](https://github.com/jebot-git/DomeD) provides the desktop/session,
[Ghostty XR](https://github.com/jebot-git/ghostty-xr) the terminal,
[mpvr](https://github.com/jebot-git/mpvr) video playback,
[cmuxr](https://github.com/jebot-git/cmuxr) music playback and an audio-reactive XR floor,
[fsvr](https://github.com/jebot-git/fsv-xr) file browsing,
[qimgvr](https://github.com/jebot-git/qimgvr) image viewing,
[Arc SBS](https://github.com/jebot-git/arc-sbs) depth conversion, and
[LightXR](https://github.com/jebot-git/LightXR) the optional login manager.
Each application has its own dependencies and can be used independently where
its selected backend permits.
