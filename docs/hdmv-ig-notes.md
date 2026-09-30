# HDMV Interactive Graphics: notes from reverse-engineering a real commercial disc

These notes come from inspecting a real, commercial, region-legitimate Blu-ray disc's
navigation data and Interactive Graphics (IG) bitstream with our own tooling
(`index_dump`, `bd_info`, `mobj_dump -d`, `mpls_dump -i`, `clpi_dump`, our own
`bd_menu_test` oracle) plus a small standalone decoder cross-checked against libbluray's
own real decoder source (`src/libbluray/decoders/ig_decode.c` / `pg_decode.c`, LGPL,
mirrored at `github.com/xbmc/libbluray` since the canonical `code.videolan.org` host
blocks automated fetches). No disc protection was removed or bypassed to get this data:
everything here comes from the plain, unencrypted navigation/graphics structures.

No title, filename, provider, or path from that disc is recorded here or anywhere in this
repository -- only the generic technical structure and behavior, most of which is public
BD-ROM/HDMV specification content anyway. Where an example needs a concrete clip number,
a generic placeholder (`NNNNN.m2ts`) is used.

## 1. The Interactive Composition Segment (ICS): real field layout

Our own encoder (`menu/ig.py`) already matches libbluray's decoder for the fields it
writes, confirmed field-by-field against the real source:

```
video_descriptor()        5 bytes: width(16) height(16) frame_rate_code(4) reserved(4)
composition_descriptor()  3 bytes: composition_number(16) composition_state(2) reserved(6)
sequence_descriptor()     1 byte:  first_in_seq(1) last_in_seq(1) reserved(6)
interactive_composition_length  3 bytes (24-bit)
interactive_composition_data_fragment():
    stream_model(1) | user_interface_model(1) | reserved(6)   -- 1 byte
    if stream_model == 0:                                      -- ONLY if in-mux, see 1.2
        reserved(7) composition_timeout_pts(33)                -- 5 bytes
        reserved(7) selection_timeout_pts(33)                  -- 5 bytes
    user_timeout_duration(24)                                  -- 3 bytes
    number_of_pages(8)                                         -- 1 byte
    page()... one per number_of_pages
```

### 1.1. `stream_model` / `user_interface_model`: exact bit meaning

Both are packed into the single byte right after `interactive_composition_length`:

- **bit 7 (MSB), `stream_model`**: `0` = the IG stream is multiplexed with the video in
  the same `.m2ts` ("in-mux"); `1` = the IG stream is a separate SubPath clip, not
  multiplexed with the video ("out-of-mux"). A real disc's pop-up menu, delivered via a
  SubPath (section 3), used `1` here.
- **bit 6, `user_interface_model`**: **`0` = Always-On, `1` = Pop-Up.** This is the
  opposite of what a generic web search on the topic returned during this investigation
  -- the authoritative value is the one in libbluray's own header comment
  (`ig.h`: `uint8_t ui_model; /* 0 - always on, 1 - pop-up */`, also exposed as the
  `IG_UI_MODEL_ALWAYS_ON` / `IG_UI_MODEL_POPUP` defines). Getting this bit backwards was
  tried and empirically verified to break libbluray's own automatic menu draw (the
  decoder stops drawing the IG overlay on its own, since a Pop-Up model waits for an
  explicit "show popup menu" key instead). **Trust the source over a generic search
  result for a single bit's polarity.**

### 1.2. `composition_timeout_pts` / `selection_timeout_pts` only exist when `stream_model == 0`

This is easy to miss by inspecting the field list alone: these two 5-byte (33-bit value +
7 reserved bits) fields are **only present in the bitstream when `stream_model == 0`**
(in-mux). For an out-of-mux (SubPath) composition, the bitstream goes straight from the
1-byte flags field to `user_timeout_duration`. Assuming these fields are always present
shifts every following field (including `number_of_pages`) by 10 bytes and produces
nonsense results (in this investigation, it initially looked like a real menu had zero
pages, which was wrong).

### 1.3. Fragmentation of large segments: continuation fragments repeat the fixed header

A `interactive_composition_length` (or the equivalent length field on other segment
types) can be larger than a single PES packet's segment payload allows (observed real cap:
**65519 bytes** of segment payload per fragment, not the theoretical 0xFFFF/65535 --
apparently a small, fixed margin left for the PES header). When a segment's data does not
fit in one fragment, the encoder splits it in multiple physically separate segments (each
its own `segment_type` + `segment_length` unit, each carried in its own PES packet):

- **Every fragment repeats the full 9-byte fixed header** (`video_descriptor` +
  `composition_descriptor` + `sequence_descriptor`), not just the first one.
- Only the **first** fragment additionally carries the segment's total-length field
  (`interactive_composition_length` for an ICS); continuation fragments go straight into
  raw continuation data after their own repeated 9-byte header.
- `sequence_descriptor.first_in_seq`/`last_in_seq` marks which fragment is first/last;
  a reader must reassemble by concatenating each fragment's payload **after stripping its
  own repeated 9-byte header** (only the first fragment's copy of those fields is
  meaningful). Naively concatenating whole fragment payloads corrupts the data from the
  second fragment onward -- this was confirmed the hard way in this investigation: the
  corrupted reassembly produced a page with an implausible field value, which looked at
  first like "the disc data is short/corrupted" but was actually our own reassembly bug,
  confirmed by cross-checking byte-for-byte against libbluray's real decoder source.
- Our own `ig.py`/`pgs_carrier` do not implement any fragmentation today (segments must
  stay under ~64 KB); this is the exact real-world format to follow if that's ever needed
  (e.g. a much larger button bitmap or a page-heavy menu).

## 2. Page / Button / BOG structure: confirmed against a real, dense menu

A real commercial "scene selection" style menu can be one composition with many pages
(one real example had **17 pages** in a single composition, `number_of_pages` matching
exactly), each page holding one or more Button Overlap Groups (BOGs), each BOG holding
one or more buttons. Observed, real, page-by-page structure (button counts per page
varying widely, from 1 up to 67 in a single page) confirms:

- `palette_id_ref` is commonly set to match the page's own index in a sequential,
  incrementing menu (page 0 uses palette 0, page 1 uses palette 1, etc.) -- a simple,
  practical convention worth following for a paginated menu with a distinct look per
  page/section.
- Buttons on a real, complex menu carry a **large** number of navigation commands each
  (double digits, sometimes 60-140+ twelve-byte HDMV instructions per button) -- this is
  not unusual or a sign of a decode error; real authoring tools generate a lot of PSR/GPR
  bookkeeping per button (matching the same pattern seen in this disc's MovieObject
  program for First Playback/Top Menu, which also had hundreds of simple register-setup
  instructions).
- `default_selected_button_id_ref` / `default_activated_button_id_ref` of `0xFFFF`
  (`NONE_ID`) is common on pages that are not meant to be entered with a specific default
  selection.

### 2.1. The ICS header, field by field, confirmed exactly against a real 17-page menu

Decoding the reference disc's own out-of-mux IG clip's ICS in full (concatenating its two
`INTERACTIVE_COMPOSITION` fragments, then walking `video_descriptor`, `composition_descriptor`,
`sequence_descriptor`, `interactive_composition_data_fragment`, all 17 pages and every button in
them) against `menu.ig.InteractiveComposition`/`Page`/`Button`'s own `pack()` layout found no
structural divergence -- every field lines up byte-for-byte with what our own encoder produces
for the same field:

- `video_descriptor`: `1920x1080`, `frame_rate_code = 1` (23.976p) -- matches the table our own
  `FRAME_RATE_CODES` uses.
- `composition_descriptor.composition_state = 2` (`EPOCH_START`) -- matches our own default.
- `stream_model = 1` (out-of-mux), and, exactly as `InteractiveComposition.pack()` already does,
  **no `composition_timeout_pts`/`selection_timeout_pts` fields at all** when `stream_model == 1`
  -- confirming these two 5-byte fields are genuinely omitted for an out-of-mux composition, not
  just zeroed.
- `user_timeout_duration = 0`, matching our own default.
- Effect sequences (`IN_effects`/`OUT_effects`) are length-prefixed and were skipped rather than
  fully decoded (irrelevant to a static menu; our own encoder never emits any window/effect data
  either, matching the "no effects" pages seen on most of the real disc's own pages).

### 2.2. A real "resume playback" button reads its title number through the same scratch register we do

The reference disc has a persistent button, present on multiple pages at the same screen
position, whose command list (24-28 commands, mixed with unrelated state bookkeeping specific to
that disc's own authoring, e.g. a legal-notice/logo-reel gate unrelated to any menu architecture
question) ends with exactly this pattern before jumping to the movie:

```
MOVE   r4076, <title number>   (an immediate)
JUMP_TITLE  r4076
```

`r4076` (`0xFEC`) is the *exact* scratch register our own `movie_object._TITLE_REG` uses for
`JUMP_TITLE` in every button we generate (`menu/simple.py`'s Play button and each Scenes button
via `_chapter_jump`). This was not something we had matched on purpose -- both landed on the same
register independently, which is a strong (if informal) confirmation that a plain
"load an immediate into a scratch GPR, then `JUMP_TITLE` that register" is the standard,
unremarkable way real authoring tools do this, not something a stricter decoder would reject if
done differently. No wait, timer, or polling-style instruction (`SET_NV_TIMER`, `STILL_ON`, or a
`GOTO` loop) appears anywhere in this button's command list, or in any other button's command list
inspected on that disc -- ruling out "the ICS button commands are structurally different from a
real disc's" as an explanation for any button-press delay bug (see 4.6).

## 3. Menu delivery models: in-mux vs. out-of-mux (SubPath)

Two different, both spec-legal, ways to deliver an IG menu were found in the wild on
different titles of the same disc:

- **In-mux**: the IG stream is multiplexed into the same `.m2ts` as the video it overlays
  (`stream_model = 0`). This is what our own menu implementation does today.
- **Out-of-mux / SubPath**: the IG stream lives in its own, separate `.m2ts` clip, only
  referenced by the playlist as a SubPath of the main video's PlayItem
  (`stream_model = 1`). The playlist's stream number table lists it with
  `SubPath Id`/`SubClip Id` fields (visible via `mpls_dump -i`) instead of being a
  regular stream of the main PlayItem.
- The CLPI (`.clpi`) of a SubPath IG clip declares a dedicated
  **`application_type = 0x05`** ("Sub TS for a sub-path of Interactive Graphics menu"),
  visible via `clpi_dump -c`. A main-path movie clip uses `application_type = 0x01`
  ("Main TS for a main-path of Movie"). This field is how a reader (and how we would need
  to author a matching CLPI) declares which role a clip plays.
- A pop-up menu delivered this way is not necessarily "the main menu screen": on the disc
  inspected, the Top Menu's own MovieObject program did not draw any visible menu at all
  -- it only read/restored player registers (audio/subtitle/angle/chapter state) around
  resuming playback (`PLAY_PL`) or returning to First Playback (`JUMP_TITLE`). The actual
  button menu was a SubPath IG overlay shown *during* a title's own playback, not a
  separate initial "menu title".
- No MovieObject command in that disc used `SET_BUTTON_PAGE`/`ENABLE_BUTTON`/
  `DISABLE_BUTTON` anywhere -- consistent with page switching happening entirely through
  each button's own command list inside the IG composition (the same model our own
  `Button.commands` already uses), never through a MovieObject.

## 4. How a real disc loops a menu's background without ever resetting it

A naive way to loop a short background clip behind a persistent IG menu is a MovieObject
that plays the clip's playlist and jumps back to itself (`PLAY_PL`; `JUMP_OBJECT` to its own
id) once the clip ends. **A real commercial disc does not do this**, and for a concrete,
verifiable reason found in libbluray's own player source (`src/libbluray/bluray.c`,
`src/libbluray/decoders/graphics_controller.c`): re-selecting a playlist -- `bd_select_playlist`
/ `_open_playlist`, which is what any `PLAY_PL` execution does -- unconditionally reloads any
out-of-mux IG SubPath (`_preload_subpaths`) and resets the current menu page back to page 0
(`GC_CTRL_INIT_MENU` always calls `_select_page(gc, 0, 0)`, regardless of which page was
showing). Looping a short clip this way means the menu silently snaps back to its first page,
and re-decodes its SubPath, every time the clip's short duration elapses -- however long or
short that is. This was found and confirmed the hard way: our own first out-of-mux
implementation did exactly this with a 10-second clip, which looked like intermittent,
worsening unresponsiveness on real players (VLC/Kodi) the longer a session went on, because
every ~10 seconds spent on a page other than the first risked losing it.

Inspecting the disc's actual menu playlist (`mpls_dump -p`/`-i`) shows the real technique:
**the same short PlayItem is repeated hundreds of times, back-to-back, inside one playlist
that is opened only once.** On the disc inspected, one playlist listed **501 PlayItems**, all
pointing at the exact same ~47-second clip, for a declared total duration of 390 minutes --
only the *first* PlayItem was `Connection Condition: Non-seamless (01)`; the other 500 were
byte-for-byte identical except for `Connection Condition: Seamless (05)`. Every one of the 501
PlayItems also repeated the very same STN table entry for the IG SubPath stream
(`SubPath Id 00` / `SubClip Id 00`), again byte-for-byte identical to the first.

This matters because of an equally concrete fact from the same player source: moving from one
PlayItem to the next *inside an already-open playlist* (`nav_next_clip`, the normal
end-of-PlayItem advance) **never** calls `_preload_subpaths` or `GC_CTRL_INIT_MENU` again --
those only run once, when the playlist is first opened. So a playlist authored this way lets
its background "loop" hundreds of times while the IG menu's page/button state (and the
decoded SubPath data) stays completely undisturbed -- the playlist is, from the player's
perspective, never reopened. The only moment a real disc like this one *does* reset is if the
entire 390-minute playlist genuinely plays out to its end, in which case the Top Menu's own
MovieObject resumes right after its `PLAY_PL` and does `JUMP_TITLE(0)` -- the reserved title
number that means "go to the Top Menu" (`BLURAY_TITLE_TOP_MENU` in libbluray's `bluray.h`, not
First Playback, which is the different reserved value `0xFFFF`) -- i.e. it loops back to
itself, just the once, after a duration long enough that no real viewing session realistically
reaches it.

### 4.1. The repeat *count* matters more than the total duration

Chasing the disc's exact total duration (390 minutes 24 seconds) with our own, much shorter
background clip (10 seconds by default) at first seemed like the most faithful thing to do --
but it isn't, and testing on a real player caught this quickly. Reaching that same duration
with a 10-second clip instead of the disc's own ~47-second one needs roughly 2343 repeated
PlayItems instead of 501, and **each PlayItem, even a byte-for-byte repeat of the one before
it, is opened independently at playlist-open time**: libbluray's own `nav_title_open`
(`src/libbluray/bdnav/navigation.c`, via `_fill_clip`) calls `clpi_get()` -- opening and
parsing the clip's CLIPINF from scratch -- once per PlayItem, with no check for "already read
this exact clip_id":

```c
clpi_free(clip->cl);
clip->cl = NULL;
file = str_printf("%s.clpi", mpls_clip[clip->angle].clip_id);
clip->cl = clpi_get(title->disc, file);
```

With thousands of repeats this means thousands of redundant re-opens of the same small file
before the menu is even interactive, and again every time the playlist is otherwise reopened.
On a synthetic test oracle running off a warm local filesystem cache this cost is invisible
(reopening the same small file thousands of times is essentially free), which is exactly why
our own test suite never caught it -- but on a real player it measurably added many seconds
between pressing a button and the corresponding title/page change actually happening, growing
with the repeat count. The disc inspected keeps its own repeat count around 501, presumably
because that already comfortably outlasts any real session at its own clip length -- **that
repeat count, not the total duration it happens to add up to, is the number worth matching.**
Our own menu now repeats its background PlayItem **500 times** flat, regardless of how long
its own (much shorter) clip is, rather than solving for a specific total duration.

**Practical takeaway for authoring**: don't loop a short menu background by having a
MovieObject reopen its playlist -- repeat the same PlayItem seamlessly inside one playlist
instead, as above. But size that repeat count to what a professionally authored disc is known
to get away with (hundreds, not thousands): the total duration it produces is a side effect of
the repeat count and the clip's own length, not something to solve for independently by
inflating the repeat count with a much shorter clip. The disc inspected's own per-repeat clip
duration is `(Out-Time - In-Time) / 45000` = `(2628256 - 524280) / 45000` = **~46.76 seconds**.

### 4.2. Every repeated PlayItem needs its own PlayListMark, not just the first

A second, separate difference from the disc inspected, found by decoding the raw
`PlayListMark()` bytes of its menu playlist directly (not just `mpls_dump`'s summary): it has
**one PlayListMark per PlayItem**, not one for the whole playlist. All 501 marks are
`mark_type` Entry (chapter), each `play_item_ref` pointing at its own PlayItem index (0, 1, 2,
... 500), and each `time` equal to that PlayItem's own `In-Time` (524280 on every single one,
since every repeat replays the same clip from the same point):

```
mark   0: play_item_ref=0,   time=524280
mark   1: play_item_ref=1,   time=524280
...
mark 500: play_item_ref=500, time=524280
```

Our own `loop_play_item` originally repeated the PlayItem itself (and, for free, whatever is
registered on its own STN table) but left the playlist's `PlayListMark()` block untouched --
still just the single mark a one-PlayItem playlist starts with. This was found while chasing a
real, reproducible playback bug (menus freezing or responding very slowly on both VLC and Kodi
after some time on the menu, independent of which button was pressed) and is a genuine,
confirmed structural gap, not a cosmetic one -- `loop_play_item` now adds one Entry mark per
repeat, referencing that repeat's own PlayItem index, matching the disc inspected exactly.
Whether this was the actual cause of that playback bug still needs to be verified on a real
player; it is documented here because it is a confirmed difference either way.

### 4.3. Refuted leads: PCR discontinuity and `is_ATC_delta`

Two leads chased while looking for the cause of a real playback bug (VLC taking anywhere from
several seconds to over a minute to react to a button press; Kodi's video outright freezing a
few seconds in), both eventually **refuted by direct measurement or testing** -- kept here
because ruling something out is as important to record as confirming something, and because
the reasoning that led to (and then away from) each one is itself useful.

**PCR discontinuity at the repeat boundary**: repeating the same PlayItem means re-reading the
same physical clip from its own start each time, which is a real jump backward in that clip's
own PCR relative to where the previous pass left off (see docs section 5). `Seamless`
connections (`connection_condition = 5`) never queue `BD_EVENT_DISCONTINUITY` (only
`Non-seamless` ones do -- see `bluray.c`), so nothing tells the calling application's demuxer to
expect this jump. This looked like a strong candidate. **It was refuted by measuring the
reference disc's own clip directly**: its first and last PCR samples are ~4.18M ticks (about 46
seconds) apart -- a completely ordinary, linear PCR, no special trick to make the loop
boundary's jump smaller or absent. The disc has the *exact same* raw PCR discontinuity at its
own loop boundary that ours does, confirmed to still work fine on both VLC and Kodi even when a
boundary was deliberately forced. Whatever the real cause was, it was not this.

**`is_ATC_delta`**: the disc's own background clip CLIPINF (`00000.clpi`) has
`is_ATC_delta: True` with one ATC delta entry, pointing at itself (`File Id 00000, File Code
M2TS`; see `clpi_parse.c`'s `CLPI_ATC_DELTA`: a 32-bit `delta`, a 5-byte `file_id`, a 4-byte
`file_code`). Our own menu clips never set this bit. libbluray's own player logic (`bluray.c`,
`navigation.c`) never reads `atc_delta` during navigation or playback -- it is parsed and
stored but never consulted -- so it cannot explain a libbluray-based player's behavior (VLC and
Kodi both use libbluray for BD navigation) either way. Left unexplained, but not implicated.

A third early lead -- a "still image" MPEG-2 encoding for the background, inferred from a VLC
log line (`libbluray debug: Still image (7 seconds)`) -- was also refuted, this time before any
code was written: decoding the reference disc's actual video showed 1119 of 1121 sampled frames
carrying genuinely new content over the clip's ~46.78 second span, i.e. ordinary continuous
video, not a slideshow of stills. The log line's meaning was misread; always verify a player's
diagnostic log against the actual bitstream before treating it as ground truth.

### 4.4. The confirmed root cause: the background video's own codec (MPEG-2, not H.264)

The actual fix, found by elimination after the leads above: **the reference disc's menu
background is MPEG-2 video** (`Video Stream 0: Codec (0002): MPEG-2 Video`, confirmed via
`mpls_dump -i`; also confirmed as genuinely continuous, full-motion video, not a still -- see
above), while our own menu background was H.264. Switching our own background encoder from
H.264 (`libx264`) to MPEG-2 (`mpeg2video`) -- with no other change to the repeated-PlayItem
architecture, the PlayListMark fix, or anything else already in place -- **fixed both players**:
VLC's button-press response dropped from anywhere between 23 seconds and over a minute down to
1-4 seconds, and Kodi's video freeze at the ~10-second PlayItem loop boundary stopped happening
entirely, confirmed independently on both.

The precise mechanism inside VLC/Kodi that made continuously-encoded H.264 background video
specifically trigger this (as opposed to MPEG-2 encoding the same content) was not fully traced
-- the working theory is that decoding H.264 (a much heavier, more modern codec, doing several
hardware/software format renegotiation attempts visible in VLC's own log:
`trying format d3d11va_vld` -> `trying format dxva2_vld` -> falls back to software `I420`) around
the exact moment a PlayItem transition or button command needs to be processed left the video
pipeline busy/contended in a way that MPEG-2 (a much lighter, simpler codec BD-ROM has always
allowed for still/menu content, with no equivalent renegotiation dance observed) does not.
That mechanism is not confirmed at the level of the other findings in this document, but the
fix itself is: matching the reference disc's exact codec choice, not just its navigation
structure, was necessary. **A real commercial disc's own choices are not incidental even where
they seem like an implementation detail (a codec) rather than part of the BD-ROM navigation
model itself** -- this is the practical lesson of this whole investigation.

### 4.5. The IG clip's own PMT was missing its program-level descriptors -- and a real PSI bug

Comparing our from-scratch IG clip's PMT against the reference disc's own (`mpls_dump`/manual
PSI parsing, not a libbluray tool -- nothing checks this file's PMT during real playback, more
on that below) found two things:

**Missing descriptors**: every real clip inspected -- both the reference disc's own IG SubPath
clip and tsMuxeR's own output for our background clip -- carries two descriptors at the
*program* level (`program_info_length` in the PMT, before the stream loop): a
`registration_descriptor` (tag `0x05`) with `format_identifier` = `"HDMV"`, the standard way an
MPEG-TS program declares itself as BD-ROM/HDMV content, and a second, vendor-private one
(tag `0x88`, 4 bytes of payload) that even libbluray's own PMT dissector reports as
`"Unknown Private"`. Our own from-scratch PMT (`menu/ig_clip.py`) had `program_info_length = 0`
-- no descriptors at all. The `0x88` descriptor's 4 payload bytes were *not* identical between
tsMuxeR's own output and the reference disc's clip, so they are not a fixed, disc-wide constant
(possibly a per-mux counter or format hint); the tag and length being present at all, with the
reference disc's own exact bytes, is what was matched.

**A real, independent PSI conformance bug, found along the way**: comparing byte-for-byte
surfaced that our own PAT/PMT section writer (`_pat_section`/`_pmt_section`) encoded
`section_number` and `last_section_number` -- two distinct 8-bit fields, always present in that
order in any MPEG-2 PSI long-form section -- as a *single* combined byte, silently dropping one
of them and shifting every field after it (PCR PID, descriptors, the stream loop) one byte out
of its real position. Comparing the same offset against the reference disc's own PAT/PMT (both
bytes present, both `0x00`) is what caught it. This almost certainly never affected real
playback: a BD player already knows an IG SubPath's PID directly from the playlist's own STN
table (see section 3), so nothing appears to actually re-parse the IG clip's own PMT during
normal navigation -- our own test suite caught neither the missing descriptors nor the
off-by-one byte because every check computed its expected offsets from the same (buggy)
assumption on both the write and the read side, which is exactly the kind of self-consistent
but wrong result independent verification (checking against the disc's own real, external bytes
instead of our own code's assumptions) is for.

**The PAT has the same gap**: every real clip inspected (the reference disc's, and tsMuxeR's own
output for our own clips) lists **two** programs in its PAT, not one: `program_number 0` mapped
to `PID 0x1F` (the "network PID" entry) *and* the actual program mapped to the PMT's own PID. Our
from-scratch PAT (`_pat_section`) only had the second one. Both fixes are in `menu/ig_clip.py`.

### 4.6. The IG SubPath's own clip duration must not be tied to the background loop's length

A real commercial disc's own IG SubPath (see section 3) is a **short clip**: on the disc
inspected, its single, non-repeated `SubPlayItem` has `In_Time = 524280`, `Out_Time = 664064`
(both 45 kHz), a duration of about **3.1 seconds** -- synced once to the very start of the main
path (`sync_playitem_id = 0`, `sync_pts = 0`) and never repeated, regardless of the main path's
own PlayItem repeating 501 times over 390 minutes underneath it. This matches section 5's own
finding that the composition is transmitted only once and never expires
(`composition_timeout_pts = 0`): once decoded, it sits on the graphics plane on its own, and the
SubPath's own clip/`SubPlayItem` has no reason to last as long as the video does.

Our own code (`menu/__init__.py`, before this was found) built the IG clip with
`build_ig_clip(segments, seconds)` and the SubPath's `Out_Time` from that same `seconds` value --
which was the background loop's own duration (`MENU_SECONDS`, 10s by default; `--menu-seconds`
in tests can make this longer still). That made our IG SubPath's own physical duration, and its
`SubPlayItem`'s `Out_Time`, scale with an unrelated setting (how long *one repeat* of the
background video lasts) instead of being a short, fixed duration decoupled from it, like the
reference disc's own ~3.1s. Fixed: `_IG_CLIP_SECONDS` (a fixed, short constant, independent of
`menu_seconds`) now drives both the IG clip's own `build_ig_clip(..., seconds=_IG_CLIP_SECONDS)`
call and the SubPath's `Out_Time`; `_add_ig_subpath` also now refuses to build a menu whose own
`decode_gap` would not fit inside that duration, rather than silently truncating it.

This was found while investigating a user-reported bug: in Kodi, a button's action (Play,
Scenes) only took effect once the background loop's own ~10-second window ended, regardless of
when during that window the button was pressed; VLC showed a similar but cache-dependent delay.
The ICS's own button commands were ruled out as the cause (2.2), and the IG SubPath was confirmed
*not* to be cloned per background-loop repeat (3) -- structurally, both already matched the
reference disc. The 10-second stall window matching `MENU_SECONDS` exactly is a strong
coincidence pointing at this bug, but it was not proven as the actual root cause (no hardware
player or Kodi available from here to confirm) -- see `docs/known-issues.md` for the open item
and re-test after this fix.

### 4.7. A clip's real content never starts at PTS/DTS 0 -- a fixed authoring lead-in, confirmed across unrelated clips

The reference disc's own authoring tool never starts a clip's actual PES content (its own
`PTS`/`DTS` domain) at 0. Checked across three otherwise unrelated clips on the disc inspected --
the menu's background video, the menu's own IG clip, and a completely separate still-image
title's clip -- all three start their real content at the exact same instant: `524280` in the
CLPI/MPLS 45 kHz clock (`1048560` in the PES's own 90 kHz clock). This is not something derived
from any of those clips' own content (they have nothing in common otherwise): it is a fixed
lead-in constant of that authoring tool, applied uniformly.

Our own `build_ig_clip` defaulted its `pts` parameter to `0`, and `_add_ig_subpath` built its
`SubPlayItem`'s `In_Time` as `0` and its CLIPINF's `Presentation Start` as `0` to match --
diverging from this convention on all three counts. Fixed: `menu.__init__._IG_LEAD_IN_PTS`
(`1_048_560`, the same 90 kHz value) is now the `pts` passed to `build_ig_clip`, and both the
`SubPlayItem`'s `In_Time`/`Out_Time` and the CLIPINF's `Presentation Start`/`Presentation End`
(`clip_info_writer.build_clip_info`'s now-required `presentation_start` parameter, previously
hardcoded to `0`) are derived from it, so all three stay self-consistent the same way the
reference disc's own do.

Not chased: that same disc's IG clip PCR values sit around `536_874_618` -- roughly 500x larger
than this PTS/DTS lead-in, on what is evidently a separate, much larger absolute clock (already
flagged in 4.3 as a refuted rabbit hole: PCR discontinuities across repeats are normal and
harmless). There is no structural reason to make our own PCR track this lead-in, so it still
starts at 0; only the PTS/DTS domain (and the navigation fields that describe it) was aligned.

## 5. Timing: two different clocks, and a real decode-time buffer

- **PES `PTS`/`DTS` are in a 90 kHz clock**, standard MPEG convention.
- **CLPI navigation fields (`Presentation Start`/`Presentation End`, and by extension
  `IN_time`/`OUT_time` on a PlayItem) are in a 45 kHz clock** -- exactly half the PES
  clock. Comparing the two without converting looks like a mismatch/corruption when it
  is not; multiply the 45 kHz value by 2 before comparing to a 90 kHz PTS/DTS.
- **A composition's `DTS` is set meaningfully before its `PTS`** on a real disc: a large
  ICS (~120 KB combined with its palettes) had roughly **200 ms of decode headroom**
  between `DTS` and `PTS`, while small palette segments (a few hundred bytes to ~1.3 KB)
  had **no `DTS` at all** (PTS-only PES header), implying near-instant decode for tiny
  segments. `DTS == PTS` (as if decoding took zero time) is not a pattern seen on real
  discs, and is a plausible source of trouble for real decoders when the composition is
  non-trivial in size.
- The composition (ICS) itself, in every real example inspected, was **transmitted only
  once** per menu clip -- never retransmitted periodically. `composition_timeout_pts = 0`
  ("never expires") is used and relied upon; a decoder is expected to keep the
  composition on screen indefinitely from a single transmission. (This is worth noting
  because periodic retransmission was, at one point, suspected as necessary for decoder
  compatibility; real commercial authoring does not do that.)

### 5.1. `data_alignment_indicator` is always set on a real disc's own IG PES

Every one of the 725 PES packets making up a real IG clip's stream (`private_stream_1`,
`stream_id = 0xBD`) has `data_alignment_indicator = 1` in the PES flags byte (`0x84`, not the
more commonly seen `0x80`). This makes sense given the byte immediately after the PES header
is always the start of one HDMV segment: the bit is doing exactly what it says. A from-scratch
PES writer that hardcodes the flags byte to `0x80` (as ours originally did) is a real, if minor,
divergence -- unlikely to matter to a real decoder (nothing in libbluray's own PES parsing
checks this bit), but there is no reason not to match it.

### 5.2. Which segment types carry a `DTS` at all: PALETTE and END never do, ICS and OBJECT always do

Across all 725 segments of the same real IG clip, `PES_packet` header flags split cleanly by
segment type, with zero exceptions:

| Segment type | Count | `DTS` present? |
|---|---|---|
| `INTERACTIVE_COMPOSITION` (0x18) | 2 (fragmented) | yes |
| `OBJECT` (0x15) | 705 | yes |
| `PALETTE` (0x14) | 17 | **no** (PTS-only) |
| `END` (0x80) | 1 | **no** (PTS-only) |

For every PALETTE and the END segment, the PTS field carries the *current decode clock*
(see 5.3) rather than any presentation-related instant of its own -- consistent with these two
segment types costing no decode time in the model below.

### 5.3. The fine-grained per-segment decode clock: a single 8,000,000 pixels/second rate for OBJECT segments

Decoding every OBJECT segment's `(pts, dts)` pair, then grouping fragments of the same
`object_id` sharing one timestamp pair (a large button image is fragmented into several `OBJECT`
segments that are all decoded together, so they share one timing pair), gives, for **all 696
distinct objects in the clip, with zero exceptions**:

```
pts - dts == ceil(width * height * 90_000 / 8_000_000)
```

where `width`/`height` are the object's own dimensions (from its header) and `90_000` is the PES
clock. `8_000_000` is BD-ROM's own HDMV **object decode rate**: 128 Mbps at 16 bits/pixel. This
was confirmed purely empirically (by fitting the real disc's own numbers), not assumed from the
spec text, but it lines up with the commonly cited BD-ROM graphics decode rate.

Segments are decoded back-to-back along a single running clock, not independently against the
composition's own presentation time:

- the clock starts at the composition's own `dts` (see 5 above, unchanged: still
  `decode_gap(total_bytes)` ticks before the composition's `pts`);
- each OBJECT's own `dts` is wherever the clock currently stands, its `pts` is `dts` plus the
  formula above, and the clock then advances to that `pts`;
- PALETTE and END segments read the clock without moving it (5.2).

This is exactly the model `menu.ig_clip.build_ig_clip` now implements
(`_OBJECT_DECODE_RATE`, `_object_pixel_count`). Not derived: the very first gap (the
composition's own `decode_gap`, based on total *byte* size rather than pixels) is a separate,
coarser approximation -- it was not re-derived here, only confirmed consistent with the
~580-600 KB/s figure already used for it (see `menu.ig.decode_gap`'s own docstring). A real
muxer may compute that first gap by an equally precise, undiscovered rule; ~600 KB/s already
matched closely enough (within a few percent) that inventing a new formula for it was not
attempted.

## 6. A clip's file size must be a whole number of 6144-byte "aligned units"

Found while authoring a from-scratch out-of-mux IG clip (no muxer to enforce this for us):
a BDAV clip's total file size must be an exact multiple of 6144 bytes (32 source packets of
192 bytes each, the "aligned unit" size). libbluray's own SubPath preloader
(`_preload_m2ts` in `src/libbluray/bluray.c`) reads a preloaded clip in fixed 6144-byte
blocks and fails the read once less than a full block remains, which is silent (no crash,
no player-visible error) unless debug logging is explicitly enabled -- it just never
decodes the trailing content. On a SubPath IG menu this shows up as the player correctly
finding and *selecting* the IG stream (a `BD_EVENT_IG_STREAM` fires) but never drawing
anything, which looks identical to a completely broken composition until the debug log is
checked (`bd_menu_test -v` with `BD_DEBUG_MASK` set, looking for
`_preload_m2ts(): error loading ... at <offset>`). This was, once again, our own encoder's
bug (missing padding), not evidence of a spec ambiguity or a decoder quirk -- consistent
with treating every unexplained anomaly as our own bug until an independent oracle proves
otherwise.

## 7. Practical tooling notes (libbluray's own CLI utilities)

- `mpls_dump`/`clpi_dump` only accept a **relative path reliably** -- calling them with an
  absolute path can silently produce no output at all. Run them from inside the
  `PLAYLIST/`/`CLIPINF/` directory itself.
- `clpi_dump` prints nothing at all unless at least one content flag is given: `-c`
  (Clip Info, including `application_type`), `-s` (Sequence Info: ATC/STC/presentation
  timestamps), `-p` (Program Info: streams/PIDs), `-i` (CPI, PTS-to-SPN map), `-e`
  (Extent Start Table). This is documented (`"not very useful"` with no flags), not a
  bug.
- `mobj_dump -d file` disassembles every MovieObject's HDMV instructions; useful to
  confirm a menu's actual navigation logic (or lack of button-page commands) directly,
  rather than guessing from a disc's behavior alone.
- `bd_info`/`index_dump` are safe to run against protection-scrambled media too (they
  only read the unencrypted navigation data); `bd_info` explicitly reports whether AACS
  is present, which is a fast way to know before attempting anything that touches the
  actual stream payloads (which are what is actually encrypted, not the navigation data).

## 8. Second dissection pass: index.bdmv, MovieObject header flags, UO masks, EP_map, PlayItem fields

A further round of field-by-field comparison against the reference disc, this time outside the
IG clip itself: `index.bdmv`, `MovieObject.bdmv`'s own per-object header (not its button-command
bodies, already covered in section 2), the playlist- and PlayItem-level User Operation mask
tables, the IG clip's own CLIPINF `CPI`/EP_map, and the remaining PlayItem fields.

### 8.1. `MovieObject.bdmv` header flags: First Playback/Top Menu are not resumable and mask Menu-call -- fixed

`mobj_dump -d`'s header line for each object (`resume intention flag`, `menu call mask`, `title
search mask`) was checked against the reference disc's own First Playback, Top Menu, and several
movie-title objects:

| Role | `resume_intention` | `menu_call_mask` | `title_search_mask` |
|---|---|---|---|
| First Playback | 0 | 1 | 1 |
| Top Menu | 0 | 1 | 0 |
| Movie title (checked on 3 different titles) | 1 | 0 | 0 |

The pattern is coherent: a movie title is resumable and does not mask anything (both bits 0); a
navigation object (First Playback, Top Menu) is not a resumable "position" and always masks the
Menu-call UO (there is no reason to re-invoke the menu while already at a menu/navigation
object); title search is additionally masked only during First Playback (blocked during whatever
setup/intro it runs) but allowed once at the Top Menu (a numeric remote key can jump straight to
a title from there).

Our own `MovieObject` dataclass's defaults (`resume_intention=True, menu_call_mask=False,
title_search_mask=False`) already matched the movie-title row exactly -- confirmed via the same
three-object check on our own output before this fix, so `programs.py`'s `play_movie` needed no
change. `show_menu`/`start` (Top Menu/First Playback) used those same class defaults, though,
which do not match either navigation-object row above. Fixed in `menu.programs.menu_navigation`:
`show_menu` now sets `resume_intention=False, menu_call_mask=True`; `start` additionally sets
`title_search_mask=True`. Covered by
`test_first_playback_and_top_menu_header_flags_match_the_reference_disc` in `tests/test_menu.py`.

### 8.2. `index.bdmv`: structure confirmed matching; the one difference (`user_data`) is intentional

`index.bdmv`'s `IndexTable()` shape (`object_type`/`access_type`/`playback_type`/`hdmv_object_id`
for First Playback, Top Menu, and titles) was already being decoded correctly by `index_dump` on
our own output in the first dissection pass, confirming the field layout in `navigation/index.py`
matches libbluray's own parser. Checked again here at the raw-byte level for the `AppInfoBDMV()`
block specifically (`reserved`, `video_format`/`frame_rate`, `user_data`): `reserved` and
`video_format`/`frame_rate` are `0` on both discs (`video_format`/`frame_rate` = "unspecified",
already documented as matching tsMuxeR's own convention). The reference disc's own `user_data`
(32 bytes, all zero in ours) is **not** all zero on that disc -- it holds an ASCII string, almost
certainly an authoring-tool/provider identifier. Not reproduced, and not a divergence to fix: per
this repository's own rule against recording any real disc's identifying details, inventing a
fake provider string would be actively wrong, and leaving it blank is a legitimate, real choice
authoring tools make (nothing requires this field to be populated). Documented here as checked,
not silently skipped.

### 8.3. `AppInfoPlayList()`'s own UO_mask_table: non-zero and content-dependent on every playlist inspected -- not reproduced

Distinct from the ICS-level `UO_mask_table` (already covered in section 1) and the PlayItem-level
one (8.5), `AppInfoPlayList()` -- the block right after the MPLS header, before `PlayList()`
itself, holding `playback_type`/`playback_count` plus its own 64-bit `UO_mask_table` -- was
decoded at the raw-byte level (`mpls_dump` does not surface it, so this was done directly:
`reserved(1) + playback_type(1) + playback_count(2) + UO_mask_table(8) + flags(1) + reserved(1)`,
14 bytes total after the block's own `length` field, on every playlist checked).

Every single playlist on the reference disc -- both of its menus and every movie title checked --
has a **non-zero, and different**, `UO_mask_table`: e.g. the main menu's is `3cb805ff40000000`,
a secondary menu's is `0000010f40000000`, and movie titles vary between `35bffdff40000000`,
`3dbffdff40000000`, `0007fdff40000000`, `0007f9ff40000000`, `3007fdff40000000` depending on the
title. This rules out a single fixed convention (like the PTS lead-in in 4.7): the values differ
per playlist in a pattern consistent with reflecting *that playlist's own available features*
(no doubt including things like angle change, secondary audio/video, or PG/subtitle change being
masked when a given title doesn't carry those streams) rather than anything about being a menu
specifically.

Our own playlists (menu and movie alike) inherit an all-zero `UO_mask_table` from tsMuxeR's own
MPLS template; nothing in this codebase writes to this block at all (`navigation/playlist.py`
only ever patches clip ids, the STN table, and PlayItem repetition/marks -- confirmed by grep).
Not fixed: without the BD-ROM spec text or libbluray's own `mpls_parse.c` source in hand to
confirm each of the 64 bits' exact meaning, and given the values are clearly feature-dependent
rather than a fixed constant, hand-deriving and hardcoding a plausible-looking bit pattern here
would be guessing, not reverse-engineering, and risks masking a UO our own disc's menu actually
needs. Flagged here as a confirmed, real, currently-unaddressed divergence rather than something
quietly judged harmless.

### 8.4. The IG clip's own CLIPINF has no EP_map/CPI either -- confirmed matching

The reference disc's own out-of-mux IG clip's CLIPINF (`clpi_dump -i`) reports `Number Stream
PID: 0` under `CPI` -- no EP_map entries at all, on both of the disc's two IG clips checked. This
matches `clip_info_writer.build_clip_info`'s own `empty_cpi = struct.pack(">I", 0)` exactly: the
IG SubPath clip is not meant to be randomly seekable on a real disc either, not just something
our own short test clips happen to get away with. No fix needed; documented as confirmed.

### 8.5. Remaining `PlayItem()` fields (`is_multi_angle`, `stc_id`, PlayItem-level UO mask, `still_mode`/`still_time`): confirmed matching

Decoded directly from the raw bytes of each disc's menu PlayItem (offsets per the existing
`_PLAY_ITEM_TO_STN` comment in `playlist.py`): `is_multi_angle=0`, `connection_condition` already
covered (4.6/4.7), `stc_id=0`, the PlayItem-level `UO_mask_table` (a *separate* 8-byte field from
`AppInfoPlayList()`'s own, see 8.3) is all-zero, and `still_mode=0`/`still_time=0` -- identical on
both discs. These fields are entirely tsMuxeR's own output in our pipeline (`navigation/playlist.py`
never writes or patches them, only reads past them to reach the STN table it does patch), so this
confirms tsMuxeR's own defaults already happen to match the reference disc's menu PlayItem here,
not something this codebase needed to change. A still-mode PlayItem does exist elsewhere on the
reference disc (a separate still-image title, `still_time≈7s`), but that is unrelated content
(a still-image title, not a menu), not something the menu path needs.
