# BDAuthor

100% CLI tool that automates Blu-ray authoring: `.mkv` -> `BDMV/` directory (with HDMV menu, later) -> optional UDF `.iso`.
Personal project. Output plays on **hardware Blu-ray players**, so anything the BD spec forbids is an error, not a warning.

## Scope

- Input: `.mkv` only. Video: **1080p H.264 only** (1920x1080 / 1280x720). UHD 4K / HEVC is out of scope.
- Output: **BDMV** (not AVCHD). Menu is HDMV (no BD-J).
- Target media (`--media`): `dvd5`, `dvd9`, `bd25`, `bd50` (default `bd25`). DVD media means non-standard "BD5/BD9" (BDMV burned on DVD).
- **Never re-encode silently.** If a stream is not BD-compatible, the tool reports why and stops. Video and subtitles are never re-encoded (a video transcode would be a future opt-in flag that consumes the validator's report; for DVD media it would be size-driven).
- **`--transcode-audio` (opt-in, `check` and `build`)** re-encodes audio streams the validator rejects to AC3 (mono 128 / stereo 192 / 5.1 448 kb/s, 48 kHz, more than 6 channels downmixed to 5.1). The report shows a warning per converted stream and is computed on the post-conversion file (including the size estimate).
- Never lose resolution.
- Phase 1 (done): `doctor`, `probe`, `check`, `build` (no menu), `iso` (separate step: it packs an existing BDMV directory; chaining the steps into one command is a later, optional shortcut). Pending in Phase 1: an optional `--deep` packet scan (keyframe interval, peak bitrate, VFR). Phase 2 (in progress): HDMV menu generator (template-driven). Done: navigation writers, looping menu clip, Play button via IG, `build --menu`. Next: title text and a chapters page, then `plan`.

## Pipeline

`probe -> validate -> plan -> render -> emit -> (iso)`

Plan the whole disc in memory (a `Disc` model), then write `BDMV/` **once**. Do not "update" an already-written BDMV: `index.bdmv`, `MovieObject.bdmv` and playlists reference each other, and the menu needs the movie's chapters.

## Dependencies (keep them minimal, everything repo-local)

- Python >= 3.14, managed with **uv** (`uv add <pkg>`, `uv run bdauthor ...`).
- `av` (PyAV, bundles FFmpeg): probing now, transcoding later. Do **not** require a system `ffmpeg`/`ffprobe`.
- `pillow`: menu rendering. `pycdlib`: ISO (UDF 2.5/2.6; ISO is a "plus", an external tool is an acceptable last resort).
- `typer`: CLI framework.
- **tsMuxeR 2.7.0** (CLI only, never the GUI) is the only external binary. It lives in `vendor/tsmuxer/tsMuxeR`, is **git-ignored** (`vendor/`). Zip SHA-256: `ceaaa181ab70e201685b1e45260d337d1cbdb0aba1408d4fbc47e232a7e4c987`. `deps.py` looks in `vendor/` first, then `PATH`.
- **Bootstrap** (`scripts/bootstrap.py`, standard library only): `python scripts/bootstrap.py [name...]` downloads the development tools into `vendor/` from the repository's `bootstrap` pre-release, verifying each asset against the SHA-256 pinned in `scripts/bootstrap.lock.json` (committed). The `bootstrap` GitHub workflow (`.github/workflows/bootstrap.yml`, manual run) builds the libbluray tools (`hdmv_test`, `mobj_dump`, `mpls_dump`, `clpi_dump`, `index_dump`, `sound_dump`, `bd_info`, `bd_list_titles`; meson, static, no Java, only libc needed) plus `bd_menu_test`, compiled from `tools/oracle/bd_menu_test.c` and mirrors tsMuxeR 2.7.0, then attaches them plus `SHA256SUMS`. After a new run, `python scripts/bootstrap.py --update-lock` pins the published hashes (the one explicit trust step) and the lock file is committed. The libbluray tools are development-only oracles for the Phase 2 menu work: nothing may require them at runtime, and tests that use them skip when they are missing (`find_libbluray_tool` in `deps.py`, `libbluray_tool` fixture in `tests/conftest.py`).
- **Validation strategy for generated disc files**: never validate our writers with a reader of our own (circular). Use independent oracles: byte comparison with what tsMuxeR writes for the same program (the `tsmuxer_disc` fixture), and libbluray's `mobj_dump -d` / `index_dump` / `mpls_dump` / `clpi_dump` / `hdmv_test`. `hdmv_test` only reads the last KB of each playlist, so it never sees the menu: use `bd_menu_test [-k enter,...] DISC` to see the IG overlays and events (`OVERLAY plane=IG cmd=DRAW ...`, `EVENT TITLE 1`). Passing them still does not prove a hardware player accepts the disc.
- Muxing goes through a `Muxer` interface (`mux/base.py`) so a pure-Python muxer can replace tsMuxeR later.

## Layout

```
src/bdauthor/
  cli/            # thin layer, one file per command (doctor, probe, check, build); root Typer app in __init__.py
                  #   _report.py renders a Report; _options.py shares --media/--transcode-audio; NB: in the package namespace `bdauthor.cli.build` etc. are the command functions
  deps.py         # locate tsMuxeR / verify environment
  exitcodes.py    # ExitCode enum
  model.py        # dataclasses: Media, MediaInfo, streams, Finding, Report; to_jsonable()
  h264.py         # minimal SPS parser (refs, interlacing, bit depth) for avcC extradata
  probe.py        # PyAV -> MediaInfo
  validate/       # one module per rule group (container, video, audio, subtitles, size) -> Report
  mux/            # base.py (Muxer interface, MuxError) + tsmuxer.py (meta generation + subprocess)
  transcode.py    # AudioPlan, validate_with_audio_transcode (report on the converted file), encode_ac3 (PyAV)
  bdmv.py         # REQUIRED_FILES, DISC_DIRECTORIES, directory helpers shared by build and iso
  iso.py          # BDMV directory -> ISO with pycdlib (build_iso, IsoError), written to *.part then renamed
  build.py        # probe -> validate -> [re-encode audio] -> prepare output -> mux -> verify BDMV
  navigation/     # Phase 2 (M1): writers for index.bdmv (index.py) and MovieObject.bdmv (movie_object.py: instruction builders, pack_movie_objects); write_navigation() also writes the BACKUP/ copies
                  #   playlist.py: patches an .mpls (retarget_playlist, clip_ids, relabel_graphics_as_interactive); clip_info.py: patches a .clpi (relabel_stream, program_map_pid)
  menu/simple.py  #   simple_menu(video, label="Play", title=None); render.draw_title draws the title, composited onto the background clip (menu/background.py: title_position, write_black_video(..., title=...)) -- it is static, so it is not part of the IG
  bdav.py         # .m2ts patching: convert_graphics_to_interactive (PID 0x1200 -> 0x1400, PMT stream type 0x90 -> 0x91 + CRC, ICS type 0x16 -> 0x18), crc32_mpeg
  menu/           # Phase 2: add_menu(bdmv_dir, video, muxer) adds a looping menu with a Play button (clip/playlist 00001, movie = title 1); `bdauthor build --menu [--menu-title NAME]` uses it (check_menu_supported runs before the mux)
                  #   ig.py = IG segment encoders (ICS, palette, object + RLE, END; pgs_carrier); render.py = Pillow buttons -> palette/indexed images;
                  #   simple.py = the Play-button menu; programs.py = which movie object does what; background.py = the black clip + the mux
tools/oracle/     # bd_menu_test.c: our own libbluray-based oracle (plays a disc from the start, prints overlays/events, can press keys); built by the bootstrap workflow
scripts/          # bootstrap.py + bootstrap.lock.json (download pinned dev tools into vendor/)
.github/workflows/bootstrap.yml   # manual workflow that builds and publishes those tools
tests/            # pytest; builders.py = model factories, conftest.py builds synthetic mkvs with PyAV
```

## Development

- Known problems, limits and what has (not) been verified on real players: `docs/known-issues.md`. Add new findings there.

- `uv run pytest` runs everything. Tests generate tiny synthetic mkv files with PyAV (libx264), so no media files are needed. Tests that call the real tsMuxeR use the `tsmuxer` fixture and are skipped when the binary is missing.
- Manual checks with a real file: `uv run bdauthor probe|check|build FILE ...`; add `-v` to see the tsMuxeR command and generated meta file.

## tsMuxeR facts (verified with 2.7.0)

- `tsMuxeR <file>` (detection mode) lists tracks (`Track ID`, `Stream ID`, `Stream lang`). The build muxes **exactly the tracks tsMuxeR detects** (IDs come from that output, never guessed from PyAV indices) after checking the video/audio/subtitle counts match the probe.
- `MUXOPT --blu-ray` with a directory as output creates `BDMV/` (index, MovieObject, PLAYLIST, CLIPINF, STREAM, BACKUP). It also creates `CERTIFICATE/BACKUP/` and empty `BDMV/{AUXDATA,BDJO,JAR,META}` and `BDMV/BACKUP/{BDJO,JAR}` directories: they belong on the disc, so any listing or copy of a BDMV must keep **empty directories** (a `find -type f` misses them).
- mkv chapters become `--custom-chapters=hh:mm:ss.mmm;...` (a chapter at 0 is added if missing). No chapters are invented when the mkv has none.
- `--blu-ray` with an output name ending in `.iso` makes tsMuxeR write an ISO during the mux, but it cannot pack an existing directory (so it cannot include a future menu). Its ISO is **pure UDF 2.50 with a Metadata Partition** (BEA01/NSR03/TEA01 volume descriptors, no ISO9660 `CD001`), the layout ImgBurn also writes; pycdlib cannot even open it ("must have at least one PVD").

## ISO (`bdauthor iso DIR -o disc.iso`)

- Built from the directory only, never from the mkv. Only `BDMV/` and `CERTIFICATE/` are included; other files in the directory are ignored. Checks the content against `--media` capacity and reads the image back to compare file list and sizes.
- pycdlib writes a **UDF 2.60 + ISO9660 (level 3) bridge**, not pure UDF 2.50 with metadata partition. Files over 4 GiB work (checked with a 4.2 GiB file). Hardware players/burners that require a strict BD image may reject it: the safest path to a disc is burning the BDMV directory (e.g. ImgBurn). A pure-UDF 2.50 writer would be the way to close this gap, using tsMuxeR's ISO as a byte-level reference.
- Not yet verified on a hardware player.
- tsMuxeR also has `--avchd`; not used (BDMV only).
- **tsMuxeR ignores the audio start offset stored in an mkv** (audio and video both start at the same time in the m2ts). The build compensates with `timeshift=<ms>ms` = audio start - video start (from the probe's `start_time`), for remuxed and re-encoded audio alike. `timeshift` accepts negative values.
- Re-encoded audio is written by PyAV to a temporary **audio-only mkv** (keeps timestamps and language) that the meta file references instead of the source's audio track. Audio tracks are matched to probe streams by order and codec family (`plan_tracks`); it refuses to guess on any mismatch.
- PyAV facts: `stream.id` is 0 for every mkv stream (not the track number), `container.chapters()` is a method, an unknown `level` is -99 or 0, and an AAC 7.1 *encoder* segfaults (use FLAC 7.1 for test fixtures).

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

- **Everything in this repository is written in English**: code (function, variable, class and module names), comments, docstrings, documentation (`AGENTS.md`, `docs/`), CLI help/messages, test names, commit messages. This holds regardless of the language used to talk with the user in the session.
- The user writes in Portuguese; reply to them in Portuguese in the session only.
- Discuss design before big implementations; prefer the smallest change that fits the surrounding code.

## IG menus (how they get into the disc)

- tsMuxeR cannot mux interactive graphics, but it carries PGS `.sup` files and drops unknown segment types. So the IG segments go in as a `.sup` (`ig.pgs_carrier`, the ICS disguised as PGS type 0x16), tsMuxeR writes them as a PGS stream (PID 0x1200, one segment per PES packet, PTS only), and afterwards `bdav.convert_graphics_to_interactive`, `clip_info.relabel_stream` and `playlist.relabel_graphics_as_interactive` relabel it as IG (PID 0x1400, coding type 0x91, playlist STN counters PG 1->0 / IG 0->1). Only fixed-size fields change, so packet timing and the EP map stay valid.
- Raw `.sup` streams have no track id in tsMuxeR's meta (`Track.id is None`).
- Verified with libbluray only (VLC and hardware pending): the ICS reserved bits are written as 0, and the IG has no DTS (tsMuxeR writes PTS only); a strict player may care.
