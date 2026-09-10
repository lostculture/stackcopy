# Canon depth-composite import with DNG conversion

**Date:** 2026-09-09
**Status:** Approved design, not yet implemented
**Target:** Upstream contribution to `AlanRockefeller/stackcopy`

## Problem

Stackcopy separates in-camera focus-stack outputs from the frames that fed
them, but it only understands Olympus/OM System cards. Canon bodies that shoot
focus bracketing with in-camera depth compositing produce the same shape of
output — a run of RAW frames followed by a single composite JPG — and stackcopy
mishandles every part of it.

A second need sits on top of import. Helicon Focus 8 fails to open the CR3
files this camera writes, so restacking requires DNG. Converting after import
means walking 65 GB twice; converting during import means one pass.

## Evidence

All findings below were measured against a real card
(`H:\DCIM\108EOSR6`, Canon EOS R6 Mark II, RF 100mm F2.8 L MACRO IS USM),
2,437 files / 65 GB / 101 composites / 2,336 CR3 frames, using ExifTool 13.59.

### Four defects in the current code

| Location | Defect |
|---|---|
| `stackcopy.py:3299` `RAW_EXTENSIONS` | `.cr3` absent. Every CR3 classifies as "other data". |
| `stackcopy.py:253` `NUMERIC_STEM_REGEX` | `\d{6,}` matches Olympus `P7310001`; Canon's `IMG_0001` has four digits, so sequence grouping never engages. |
| `stackcopy.py:262` `MAX_STACK_INPUT_FRAMES` | Capped at 15. Observed groups run to 29; Canon bracketing allows 999. |
| `stackcopy.py:11-30` MakerNote path | Olympus `StackedImage` only. No Canon equivalent is read. |

### Canon metadata behaves differently from Olympus

Canon writes `FocusBracketingDepthComposite` in its MakerNote. Measured across
all 101 composite JPGs on the card, it reads `On` for every one — including
every short group (1, 2, 3, 4 and 6 frames), which confirms those are genuine
short or aborted bracketing runs rather than stray JPG-only shots.

Two properties make it weaker than the Olympus tag, and both constrain the
design:

1. **It is present on the source CR3 frames too, identically.** It records the
   camera *setting*, not "this file is the composite". It can gate a JPG, but it
   cannot label a frame.
2. **`FocusBracketingImageCount` is the configured count, not the actual one.**
   `IMG_0096.JPG` reports 25 and has 29 preceding frames. Using it as a group
   boundary would corrupt those groups.

### The timestamp anchor

A composite JPG's `SubSecDateTimeOriginal` is byte-identical to that of the
**first frame** of its stack. Measured across all 101 composites in `108EOSR6`:

| Outcome | Count |
|---|---|
| Anchor frame found within the preceding CR3 run | **100** |
| — of which the anchor is the run's first frame (naive splitting also correct) | 95 |
| — of which the anchor is mid-run (**naive splitting wrong**) | 5 |
| No anchor in this folder (stack crosses a folder boundary) | 1 |

The distinction matters. On the five mid-run cases the anchor is correct and
walking back over the whole contiguous run silently absorbs unrelated frames:

| Composite | Naive run | Anchor frame | True count | Frames naive would have added |
|---|---|---|---|---|
| `IMG_0096.JPG` | 29 | `IMG_0071` | 25 | 4 (`IMG_0067-0070`, shot ~1 hour earlier) |
| `IMG_1957.JPG` | 28 | `IMG_1932` | 25 | 3 (`IMG_1929-1931`, shot ~50 s earlier) |
| `IMG_0175.JPG` | 26 | mid-run | 25 | 1 |
| `IMG_0929.JPG` | 26 | mid-run | 25 | 1 |
| `IMG_2412.JPG` | 26 | mid-run | 25 | 1 |

Every true stack resolves to 25 frames, matching the camera's bracketing
setting. The single remaining composite, `IMG_0014.JPG`, is the cross-folder
case below.

Naive run-splitting would have silently poisoned five stacks with unrelated
RAW frames. The anchor is therefore not merely an optimisation over
sequence-and-timestamp grouping; it is a correctness fix.

### Cross-folder stacks are real on this card

`IMG_0014.JPG` anchors at `10:43:59.28`, which is *earlier* than
`IMG_0001.CR3` (`10:44:02.03`). Its missing frames are at the tail of the
previous camera folder: `107EOSR6\IMG_9995–9999.CR3` run `10:44:00.88` to
`10:44:01.80` at the same 0.23 s interval as `108EOSR6\IMG_0001–0013.CR3`.
The stack spans the 9999 → 0001 folder rollover.

At that 0.23 s cadence the anchor timestamp lands on `IMG_9988.CR3`, making the
stack `IMG_9988`-`IMG_9999` (12 frames) plus `IMG_0001`-`IMG_0013` (13) = **25**,
in line with every other full stack on the card. `107EOSR6\IMG_9987.JPG` is the
*previous* stack's own composite and is correctly excluded. Confirmed against
the captured fixture.

The card holds nine camera folders (`100EOSR6` … `108EOSR6`).

### Cadence is relative, not absolute

Observed intra-stack intervals are uniform but vary by exposure: 0.07 s for
1/6 s exposures, 0.23 s for the slower run. Adding flash will change this
again — shorter exposures shorten the interval, while variable flash recycle
adds noise that grows as the battery drains.

Any cadence test must therefore be **relative to the run being examined**, never
an absolute threshold. What distinguishes a stack from ordinary shots is the
*consistency* of the interval, not its magnitude.

## Design

### Component 1 — `canon_stacks.py`, detection

A pure function over metadata records. Input: a list of
`(path, folder, number, ext, depth_composite, timestamp)`. Output: a list of
`CanonStackGroup(output_jpg, frames, confidence, notes)`. No I/O, so it is
fully testable from fixtures with no camera card present.

For each JPG that has no same-numbered CR3:

1. **Gate.** Require `FocusBracketingDepthComposite == On`. This separates a
   composite from an ordinary JPG-only frame.
2. **Anchor.** Take the JPG's `SubSecDateTimeOriginal`. Walk backward through
   numerically consecutive CR3s and cut the group at the frame whose timestamp
   matches exactly. Confidence: `anchor`.
3. **Cadence fallback.** If no frame matches, compute the median inter-frame
   interval of the candidate run and walk backward while each gap stays within
   4× that median. Cut where the cadence breaks. Confidence: `cadence`, and the
   log names it.
4. **Cross-folder reach.** If the backward walk reaches the folder's first file
   with the anchor unfound, reach into the numerically adjacent previous camera
   folder under upstream's existing `previous_adjacent_camera_dir` guards. A
   borrowed frame must also satisfy `MAX_CROSS_FOLDER_INPUT_GAP_SECONDS`.
   Confidence: `anchor-crossfolder`.
5. **Never bound a group by `FocusBracketingImageCount`.** Compare against it
   and emit a note on disagreement; never let it set an edge.

Groups that cannot be resolved are reported, never silently truncated.

**ExifTool policy.** Upstream keeps ExifTool optional and degrades to
heuristics; Canon detection honours the same contract. Reuse `exiftool_info()`
(`stackcopy.py:2609`), `_bundled_exiftool_path()` (`:2494`) and the
`STACKCOPY_EXIFTOOL` override (`:2557`) rather than adding discovery logic.
Without ExifTool, step 1 degrades to "JPG with no matching CR3" and step 2 to
pure cadence, with one concise warning naming the affected count — matching
upstream's existing degradation reporting.

### Component 2 — DNG conversion stage

Wraps Adobe DNG Converter as a batch step: one invocation per stack folder
(101 invocations, not 2,336).

- **Discovery.** Probe per-platform install locations
  (`C:\Program Files\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe`;
  `/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter`),
  overridable by env var. If absent, the Canon mode refuses up front with a
  clear message rather than half-importing.
- **Options.** Lossless compressed, medium embedded preview, no embedded
  original. Exact flag spellings are to be confirmed against the installed
  binary's own help output during implementation, not assumed from
  documentation.
- **Failure stops the group.** An incomplete stack handed to Helicon is worse
  than no stack. A conversion failure leaves that group unwritten and reported;
  other groups continue.
- **Resume.** A group whose DNG count already equals its frame count is
  skipped, so an interrupted run resumes.
- **Parallelism.** Conversion is CPU-bound and single-threaded per file, and
  dominates wall-clock for the whole import. Parallelise across stack folders
  via `--dng-jobs`.

### Component 3 — Layout and CLI

```
F:\Stacked Images\
  2026-07-31\                       <- EXIF DateTimeOriginal (see note)
    IMG_0014\
      IMG_99nn.dng … IMG_0013.dng   <- 25 frames, reaching into 107EOSR6;
                                    the first frame is whichever 107EOSR6
                                    CR3 carries the anchor timestamp
      IMG_0014.JPG                  <- camera composite, for reference
    IMG_0096\
      IMG_0071.dng … IMG_0095.dng   <- 25, not the naive 29
      IMG_0096.JPG
F:\Single Images\                   <- loose CR3s, copied as-is, not converted
  2026-07-31\
    IMG_0067.CR3 … IMG_0070.CR3
```

Two deliberate decisions:

- **Dated by EXIF `DateTimeOriginal`, not filesystem mtime.** Upstream uses
  mtime. Detection already reads `DateTimeOriginal`, so using it costs nothing
  and is strictly more accurate: mtime does not survive a file copy.
- **Loose CR3s go to a sibling root, outside the stacks tree.** Placed inside
  it, Helicon's "Add folders" recursion would treat the singles folder as a
  stack and render from unrelated frames.

New sixth mode:

```
stackcopy.py --canon-import SRC_DIR DEST_DIR
             [--singles-dir DIR] [--dng-jobs N] [--no-dng]
```

Upstream's `--dry`, `--verbose`, collision handling, atomic writes,
verification and low-space checks apply unchanged. `--dry` lists all groups,
their frame counts and their confidence level before any conversion runs.

The card is never modified: this mode copies only.

### Component 4 — Testing

Detection is a pure function, so it is tested from a checked-in ExifTool JSON
fixture captured from the real card (metadata only, a few KB, no image data):

- exact-anchor grouping
- cadence fallback, including a synthetic slow-flash run to prove the test is
  relative rather than absolute
- cross-folder reach (`IMG_0014`)
- `FocusBracketingImageCount` disagreement (`IMG_0096`)
- 1-, 2- and 3-frame groups
- the `IMG_0902` numbering gap
- rejection of a non-composite JPG

The conversion stage is tested against a stub converter binary, covering
failure, resume and parallelism without invoking Adobe's.

Style and structure follow the existing 17-file suite in `tests/`.

## Scope

In scope: `108EOSR6` for the first runs, with automatic reach into `107EOSR6`
for the one stack that spans the boundary. Detection and conversion are built
for the whole card so that widening scope is a flag, not a rewrite.

Out of scope: Olympus behaviour, which is untouched; moving or deleting files
from the card; and Helicon invocation, which stays manual.

## Sequencing

Run detection read-only across all 101 groups and review the plan before any
conversion. Grouping errors surface there, at no I/O cost.
