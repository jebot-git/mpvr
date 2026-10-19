# mpvr

mpv fork with GPU SBS video playback in active WayVR/WiVRn and DomeD XR
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

The local `mpv` command also launches this build. XR output fits each eye on the
GPU, retains mpv playback/audio/seek controls, and duplicates subtitles. Existing
SBS metadata and filename markers are recognized; explicit layout and eye-swap
options handle ambiguous files. Outside DomeD, only recognized SBS videos open
in XR; 2D/unverified videos stay on the desktop. DomeD can duplicate mono into
both eyes. A runtime guard hands later 2D content back to the desktop.
No depth synthesis or video conversion is performed.

[Build, installation, backend options and validation](xr/README.md).

The native Wayland change defers zero-sized viewport requests during startup,
fixing a protocol error reproduced on the live WayVR compositor. DomeD's companion
changes add mpvr stereo identity/discovery and the required viewporter protocol.

Upstream [GPL](LICENSE.GPL), [LGPL](LICENSE.LGPL) and [copyright notices](Copyright)
remain in force. New XR integration files are provided under GPL-2.0-or-later.
