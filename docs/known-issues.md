# Known issues and caveats

Problems, limits and surprises found while building BDAuthor. Each entry says what was observed, why, and what to do about it. This file is meant to be reorganised into proper documentation later.

Status legend: **open** (still a risk), **worked around** (handled in code, see notes), **by design** (accepted limit).

## Verification status

What has actually been tested, so nobody mistakes "passes the tests" for "plays everywhere".

| What | Result |
|---|---|
| Synthetic files (PyAV/libx264) through `probe`, `check`, `build`, `iso` | Covered by the test suite |
| Real file `movie.mkv` (720p24 H.264 High@3.1 + HE-AAC 5.1) with `--transcode-audio`, `build` | Plays correctly in **VLC** |
| ISO of that build (`iso ... -m dvd5`, 1.38 GiB) | Plays correctly in **VLC** |
| Any output on a **hardware Blu-ray player** | **Not tested yet.** VLC is very tolerant, so this is the real test. |

## ISO

### The ISO is a UDF 2.60 + ISO9660 bridge, not a pure UDF 2.50 image (open)

- **Observed:** the image written by `bdauthor iso` (pycdlib) has an ISO9660 layer (`CD001`) plus UDF 2.60. The ISO that tsMuxeR writes (and, to our knowledge, ImgBurn) is **pure UDF 2.50 with a Metadata Partition** (volume descriptors `BEA01`/`NSR03`/`TEA01`, no `CD001`). pycdlib cannot even open the tsMuxeR image ("must have at least one PVD").
- **Why it matters:** the Blu-ray spec expects UDF 2.50. A strict hardware player or burning program may reject a bridge image. VLC accepts it.
- **What to do now:** to get a disc, burn the `BDMV/` directory directly (e.g. ImgBurn "build" mode) instead of burning our ISO. Treat the ISO as an archive / PC-mount format until it is tested on hardware.
- **If hardware rejects it:** the fix is a pure-Python UDF 2.50 writer (volume recognition sequence, AVDP, volume descriptors, partition maps with a metadata partition, file set descriptor, file entries). tsMuxeR's ISO is a byte-level reference to compare against. This is a sizeable piece of work.
- **Why not tsMuxeR for the ISO:** it only writes an ISO *during* the mux, so it cannot pack an existing directory and would not include a future menu. It can also re-mux a `.mpls`, but that rebuilds the streams and drops anything we add.

### Other ISO notes

- Only `BDMV/` and `CERTIFICATE/` are included; anything else in the source directory is ignored on purpose.
- ISO9660-layer names are normalised (upper case, `A-Z0-9_`, at most 30 characters, e.g. `MOVIEOBJECT.BDMV;1`). Names that do not fit are an error. The UDF layer keeps the original names.
- Files larger than 4 GiB work (checked with a 4.2 GiB file). The image is verified by reading it back and comparing file list and sizes, not byte for byte (that would double the I/O of a 25 GB image).
- The image is written to `<name>.iso.part` and renamed on success, so a failure leaves no partial file.

## Disc structure

### Empty directories are part of the disc (worked around)

tsMuxeR creates `CERTIFICATE/BACKUP/` plus empty `BDMV/AUXDATA`, `BDMV/BDJO`, `BDMV/JAR`, `BDMV/META`, `BDMV/BACKUP/BDJO` and `BDMV/BACKUP/JAR`. `bdauthor iso` keeps them and its read-back check compares directories as well as files. Any tool that lists or copies a disc by files only (`find -type f`, a naive copy) loses them, which is easy to mistake for a missing `CERTIFICATE/`.

### BDMV on DVD-5 / DVD-9 is non-standard (by design)

The Blu-ray spec does not define red-laser media. A BDMV folder burned on DVD ("BD5"/"BD9") only plays on players that choose to support it, and a plain DVD player will not play it at all. `--media dvd5|dvd9` only changes the capacity budget. AVCHD would be the standardised option for DVD media but has a very limited menu.

### Chapters are not invented (by design)

mkv chapters become playlist marks (`--custom-chapters`). If the mkv has none, the disc has a single mark at the start. Future menu work (scene selection) will need to decide what to do about that.

## Menu (Phase 2, in progress)

### `add_menu` is not wired into `bdauthor build` yet (open)

The menu (Play button) works end to end in libbluray but the CLI does not offer it yet (milestone M6); use `bdauthor.menu.add_menu` from Python. Only one button exists; the chapters screen and a title are later milestones.

### Interactive graphics are verified in libbluray only (open)

`bd_menu_test` (libbluray) draws the button at the right place and ENTER starts the movie. Not yet opened in VLC or on a hardware player. Things a stricter player might reject, all unverified: the IG stream has PES timestamps with PTS only (no DTS), ICS reserved bits are 0 rather than 1, the PMT entry for the IG stream has no descriptors, and the menu clip has no audio.

### The menu clip is video only and progressive only (open)

- The black clip has no audio stream. The spec does not require one, but a hardware player that dislikes silent menus would need a silent AC3 track added.
- Interlaced movies (1080i) are refused with a clear error; the black clip would need interlaced encoding.
- The clip is 10 seconds; the menu playlist restarts when it ends, which can show a brief hitch on some players (a seamless loop needs a different playlist structure).

### hdmv_test cannot see the menu

libbluray's `hdmv_test` seeks to the last 6 KB of each playlist before reading, so it never decodes the IG. Use `bd_menu_test` (`tools/oracle/bd_menu_test.c`, shipped in the bootstrap release) for menus.

## Compatibility checks

### Metadata checks do not prove hardware compatibility (open)

`check` uses container/codec metadata plus a small SPS parser (level, profile, bit depth, chroma, interlacing, reference frames). It cannot see things that need a scan of the packets: open GOPs, keyframe spacing, peak bitrate (only the average is checked), variable frame rate, slice/bitstream constraints. A file can pass `check` and still be refused by a picky player. The optional `--deep` scan (keyframe interval, 1-second peak bitrate, VFR) is not implemented yet.

### Only 1080/720 H.264 is accepted (by design)

UHD/4K and HEVC are out of scope. Other Blu-ray video formats (1440x1080, SD sizes, VC-1, MPEG-2) are rejected too. A cropped frame such as 1920x800 is an error because Blu-ray needs a full 1920x1080 or 1280x720 frame, which means a video re-encode with black bars (not implemented; video is never re-encoded).

### Incompatible non-audio tracks stop the run (by design)

A text subtitle (SRT/ASS) or a second video stream is an error, not something silently dropped. Only audio can be re-encoded (`--transcode-audio`).

## Audio

### tsMuxeR ignores the audio delay stored in an mkv (worked around)

- **Observed:** with an mkv whose audio starts 0.5 s after the video, the muxed `.m2ts` had audio and video starting together, both for remuxed and for re-encoded audio. In a real file with a small audio delay this would be a lip-sync error.
- **Fix:** the probe records each stream's `start_time` and the meta file gets `timeshift=<ms>ms` (audio start minus video start; negative values work). Tests measure the delay in the final `.m2ts` for both paths.
- **Not verified:** on a real file with a large delay; `movie.mkv` played in sync in VLC (probably a near-zero delay).

### `--transcode-audio` details

- Everything rejected by the audio rules is re-encoded to AC3, including AC3 at a wrong sample rate. Bitrates: mono 128, stereo 192, 5.1 448 kb/s; sources with more than 6 channels are downmixed to 5.1 (default swresample matrix).
- The converted audio goes into a temporary audio-only mkv (in the system temp directory, roughly the size of the AC3 track) that tsMuxeR reads instead of the original audio track.
- Real HE-AAC could not be generated for tests (only AAC-LC), so HE-AAC was verified only through the real `movie.mkv`.

## tsMuxeR

- CLI only; the GUI is never used. Version 2.7.0, downloaded by hand into `vendor/tsmuxer/` (git-ignored). SHA-256 of the zip is in `AGENTS.md`.
- File names containing a double quote (`"`) are rejected, because the meta file quotes paths.
- Track numbers come from tsMuxeR's own detection output. If its list ever disagrees with the probe (different counts, or a different codec family for a replaced audio track) the build stops instead of guessing.
- It writes a `BACKUP/` copy of the navigation files and the empty directories described above.

## PyAV / FFmpeg

- `stream.id` is 0 for every mkv stream, so it cannot be used as the Matroska track number.
- `container.chapters()` is a method; an unknown H.264 `level` is reported as -99 or 0.
- PyAV cannot read the audio language from the `.m2ts` PMT (tsMuxeR stores it in the playlist/clip info); use tsMuxeR's detection on the `.mpls` to check it.
- Creating an **AAC 7.1 encoder** segfaults (exit 139), so test fixtures for 7.1 use FLAC. Encoding text subtitles (`subrip`) also fails without an ASS header, so subtitle handling is tested with hand-built model objects instead of real mkv files.
- The bundled FFmpeg has `libx264`, `ac3`, `eac3`, `aac` and PCM encoders.

## Environment

- On a filesystem without hardlink support, uv warns "Failed to hardlink files". It is harmless; `UV_LINK_MODE=copy` silences it.
- A large-file (`>4 GiB`) check needs a temp directory that can actually hold it; a small tmpfs `/tmp` is not enough, use a directory on a regular filesystem.
- If the system `python3` is not on PATH or is the wrong version, use `uv run python`.

## Code gotchas

- In the `bdauthor.cli` package namespace, `build`, `check`, `probe`, `iso` and `doctor` are the command **functions**, which shadow the submodules of the same name. To patch something in a command module use `importlib.import_module("bdauthor.cli.build")`.
