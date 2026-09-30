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
