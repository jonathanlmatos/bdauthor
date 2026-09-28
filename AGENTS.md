# BDAuthor

100% CLI tool that automates Blu-ray authoring: `.mkv` -> `BDMV/` directory (with HDMV menu, later) -> optional UDF `.iso`.
Personal project. Output plays on **hardware Blu-ray players**, so anything the BD spec forbids is an error, not a warning.

## Scope

- Input: `.mkv` only. Video: **1080p H.264 only** (1920x1080 / 1280x720). UHD 4K / HEVC is out of scope.
- Output: **BDMV** (not AVCHD). Menu is HDMV (no BD-J).
- Target media (`--media`): `dvd5`, `dvd9`, `bd25`, `bd50` (default `bd25`). DVD media means non-standard "BD5/BD9" (BDMV burned on DVD).
- **Never re-encode silently.** If a stream is not BD-compatible, the tool reports why and stops. Auto-transcode is a future opt-in flag that must consume the validator's report (for DVD media it would be size-driven).
- Never lose resolution.
- Phase 1 (current): `doctor`, `probe`, `check`, `build` (no menu). Phase 2: `plan`, HDMV menu generator (template-driven), `iso`.

## Pipeline

`probe -> validate -> plan -> render -> emit -> (iso)`

Plan the whole disc in memory (a `Disc` model), then write `BDMV/` **once**. Do not "update" an already-written BDMV: `index.bdmv`, `MovieObject.bdmv` and playlists reference each other, and the menu needs the movie's chapters.

## Dependencies (keep them minimal, everything repo-local)

- Python >= 3.14, managed with **uv** (`uv add <pkg>`, `uv run bdauthor ...`).
- `av` (PyAV, bundles FFmpeg): probing now, transcoding later. Do **not** require a system `ffmpeg`/`ffprobe`.
- `pillow`: menu rendering. `pycdlib`: ISO (UDF 2.5/2.6; ISO is a "plus", an external tool is an acceptable last resort).
- `typer`: CLI framework.
- **tsMuxeR 2.7.0** (CLI only, never the GUI) is the only external binary. It lives in `vendor/tsmuxer/tsMuxeR`, is downloaded manually and is **git-ignored** (`vendor/`). Zip SHA-256: `ceaaa181ab70e201685b1e45260d337d1cbdb0aba1408d4fbc47e232a7e4c987`. `deps.py` looks in `vendor/` first, then `PATH`. A setup script is out of MVP scope.
- Muxing goes through a `Muxer` interface (`mux/base.py`) so a pure-Python muxer can replace tsMuxeR later.

## Layout

```
src/bdauthor/
  cli/            # thin layer, one file per command (doctor, probe, check, build); root Typer app in __init__.py
  deps.py         # locate tsMuxeR / verify environment
  model.py        # dataclasses: MediaInfo, Stream, Report, Media
  probe.py        # PyAV -> MediaInfo
  validate/       # one function per rule -> Report
  mux/            # base.py (Muxer interface) + tsmuxer.py
```

**Thin CLI, fat core**: `cli/` only parses arguments, calls core functions and formats output. Core modules must not import Typer/Rich or print.

## CLI conventions

- git-style subcommands: `bdauthor <verb> <args>`.
- **Exit codes**: `0` ok, `1` media failed the check, `2` usage error (Click default), `3` missing dependency.
- **stdout = results, stderr = logs/progress**, so commands can be piped.
- `--json` on `probe` and `check`. `-v/--verbose` shows the exact external commands run (e.g. the tsMuxeR invocation).
- Validator rules are declarative: one function per rule, each returning `ok` / `error` / `warning`, so the report can later drive auto-transcode.

## Validation notes

- Video: H.264, 8-bit 4:2:0, level <= 4.1, 1920x1080 or 1280x720; 1080p only at 23.976/24; 1080i 25/29.97; 720p 23.976-59.94. Check `num_ref_frames` against the level's DPB limit. Cropped sizes like 1920x800 are errors (need re-encode with padding).
- Audio: AC3, E-AC3, DTS, DTS-HD, TrueHD, LPCM. AAC/FLAC/Opus are errors. Subtitles: PGS only.
- Bitrate: video <= 40 Mbps, total mux <= 48 Mbps. Size (streams + menu + filesystem overhead) must fit the chosen `--media`.
- Metadata checks do not prove hardware compatibility (open GOP, keyframe spacing, etc. need bitstream/packet analysis). Final proof is playing on a real player.

## Working with the user

- The user writes in Portuguese; reply in Portuguese. Code, comments and repo docs are in English.
- Discuss design before big implementations; prefer the smallest change that fits the surrounding code.
