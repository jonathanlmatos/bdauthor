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

### Chapters are not invented unless asked (by design)

mkv chapters become playlist marks (`--custom-chapters`). If the mkv has none, the disc has a single mark at the start, and the menu shows no Scenes page (see below) -- unless `build --auto-chapters N` is given, which adds a mark every N minutes for a source that has none of its own (a source that already has chapters is never overridden).

## Menu (Phase 2, in progress)

### `bdauthor build --menu` is experimental (open)

It adds a menu with a Play button (`--menu`), an optional title above it (`--menu-title`), and a Scenes page (a grid of numbered buttons, one per chapter mark, plus Back) whenever the source has more than one chapter -- no separate flag. Interlaced video and frame rates without an IG code are refused before the mux starts.

Each scene button sets GPR0 to its mark index, then jumps to title 1; the movie's own MovieObject (the same one tsMuxeR writes) reads that register and calls `PLAY_PL_MK` with it, resetting GPR0 to 0 afterwards -- so a plain Play still starts at the beginning. The mark index is computed the same way tsMuxeR computes them for the playlist (`chapter_marks`), so a scene button always lands where its number says even with duplicate or out-of-order chapter timestamps. Page switching (Scenes / Back) uses `SET_BUTTON_PAGE`, an IG-only command (10.4.3.4 (D) of the spec): it only works inside a button's own navigation commands, not in a plain MovieObject.bdmv program.

The title is composited directly onto the black background clip (not part of the IG stream, since it is static and does not need button states): rendered once with Pillow, alpha-blended against black in RGB before converting to limited-range BT.709 YCbCr (so the blend math is correct, not just the button-image quantised-palette path), then written into every frame's Y/Cb/Cr planes respecting `line_size`/stride.

### Interactive graphics are verified in libbluray only (open)

`bd_menu_test` (libbluray) draws the button at the right place and ENTER starts the movie. The IG delivery model, ICS bit layout, timing and playlist-loop structure were reverse-engineered against a real commercial disc and matched field-by-field; see `docs/hdmv-ig-notes.md` for the full findings. Not yet opened on a hardware player.

### Play stall and Scenes freeze in VLC/Kodi (resolved -- was our own background video codec)

- **Observed:** clicking Play or Scenes on the menu could take anywhere from several seconds to over a minute in VLC, and could freeze the video outright in Kodi a few seconds after the menu appeared, worsening the longer the menu looped.
- **Investigated and ruled out, in order:** the SubPath/PlayItem-repeat architecture matching the reference disc byte-for-byte (still froze); the repeat count (2343 vs 500, matching the reference disc's own 501 -- froze either way); missing `PlayListMark` entries (added, matches the disc now, did not fix it alone); a real PCR discontinuity at the repeat boundary (confirmed present on the reference disc's own clip too, with no ill effect); `is_ATC_delta` (libbluray never reads it during playback).
- **Actual root cause:** the menu's background video was H.264; the reference disc's own menu background is **MPEG-2**. Switching our encoder from `libx264` to `mpeg2video` (see `menu/background.py`) fixed both players, confirmed independently: VLC's response time dropped to 1-4 seconds and Kodi's freeze stopped happening entirely.
- **Why this fixed it:** not fully traced at the libbluray/VLC internals level -- the working theory is that decoding continuous H.264 (which went through several failed hardware-decoder format negotiations visible in VLC's own log) left the video pipeline busy at exactly the moment a PlayItem transition or button command needed to be processed, in a way MPEG-2 (lighter, and BD-ROM's traditional choice for menu/still content) does not. See `docs/hdmv-ig-notes.md` §4.4 for the full investigation, including the leads that turned out to be red herrings.
- **Still open:** not verified on a hardware player, or on Kodi/VLC versions other than the ones tested.

### Button presses only take effect at the end of the loop window (partially addressed, unconfirmed)

- **Observed:** in Kodi, pressing Play or Scenes has no visible effect until the current ~10-second loop window ends, no matter when during that window the button is pressed or how many times. VLC showed a similar delay with a cold player/disc cache; with a warm cache the delay looked much shorter (1-2s), suggesting read-ahead buffering plays a role, not a fixed player-side wait.
- **Investigated and ruled out:** the IG SubPath being cloned per background-loop repeat (it is not -- confirmed byte-for-byte against the reference disc's own `mpls_dump` output: a single, non-repeated `SubPlayItem`, `sync_playitem_id=0`, matching ours); the ICS's own button navigation commands (decoded in full against the reference disc's menu -- same command family, same register choice for `JUMP_TITLE` (r4076, matching `movie_object._BUTTON_PAGE_BUTTON_REG`'s sibling register exactly), no wait/timer/still instruction anywhere in a button's own command list on either disc).
- **A real, confirmed structural difference, now fixed:** the reference disc's own IG SubPath clip is short (~3.1s observed) and its `SubPlayItem`'s `Out_Time` is *independent* of how long the main path's video PlayItem/loop lasts. Our own code tied the IG clip's physical duration (and its `SubPlayItem`'s `Out_Time`) to the *background loop's own* duration (`MENU_SECONDS`, 10s by default) -- the same order of magnitude as the observed stall window. Decoupled in `menu/__init__._IG_CLIP_SECONDS` (a fixed, short duration, independent of `menu_seconds`/the background loop length); `_add_ig_subpath` now guards against a menu whose own decode time would not fit in it.
- **Not confirmed:** this was not the *proven* root cause -- there is no way to test on a hardware player or Kodi from here. It is a plausible, well-supported candidate (the suspicious 10s-vs-10s coincidence, and it is a genuine, now-fixed divergence from the reference disc), but the VLC cache-dependent behavior described above is not fully explained by it alone, and the underlying question -- what exactly a player does once a SubPlayItem's own `Out_Time` is reached, and why that might gate navigation-command processing -- was not traced at the libbluray/Kodi internals level. Re-test on Kodi/VLC/hardware after this fix and update this entry.

### The menu's playlist-level UO mask is left at tsMuxeR's all-zero default (open)

- **Observed:** the reference disc's own `AppInfoPlayList()` block (in every playlist checked, menu and movie titles alike) has a non-zero, and different per playlist, 64-bit `UO_mask_table` -- see `docs/hdmv-ig-notes.md` §8.3. Ours is always all-zero, inherited from tsMuxeR's own MPLS template; nothing in this codebase writes to this block.
- **Why not fixed:** the values differ across every playlist inspected in a pattern consistent with reflecting that playlist's own available features (audio/subtitle/angle tracks), not a single fixed convention. Without the BD-ROM spec text or libbluray's `mpls_parse.c` source to confirm each of the 64 bits' exact meaning, hardcoding a plausible-looking pattern would be guessing, and risks masking a user operation our own menu actually needs (or vice versa).
- **What to do if pursued:** get the exact bit-to-operation mapping from the BD-ROM System Description or libbluray's own parser source, derive what should be masked from what our own disc's menu/titles actually support (matching the *principle* the reference disc follows, not its literal bytes), and write it in `navigation/playlist.py`.

### The menu clip is progressive only (open)

- Interlaced movies (1080i) are refused with a clear error; the black clip would need interlaced encoding.
- The background clip loops by repeating its own PlayItem, seamlessly connected, inside the same playlist (matching a real commercial disc's own technique) rather than by a MovieObject reopening a short playlist -- see `docs/hdmv-ig-notes.md` §4 for why the naive approach resets the menu to its first page every loop.

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
