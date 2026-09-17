# flatstitch

*[Читати українською](README.uk.md)*

A Linux tool (CLI + GUI) that stitches scanned images into one seamless
TIFF. Functionally aimed at the same job as Microsoft ICE (Image Composite
Editor), but deliberately narrowed to one specific case: **stitching flat
scans**, not photo panoramas.

<img width="1080" height="866" alt="image" src="https://github.com/user-attachments/assets/8ea2b48d-5b02-4030-a70c-4ad4487206c1" />


There are two ways to run it, both built on the same core (the
`flatstitch/` package):
- **`flatstitch-gui.sh`** — a full graphical interface: add scans (button
  or drag-and-drop), tune the settings, hit "Stitch" and watch progress
  and the result right in the window.
- **`flatstitch.sh`** — a command-line version, handy for scripting and
  batch processing.

Available in 10 languages (auto-detected from your system locale, or
choose explicitly): English, Ukrainian, Spanish, French, German,
Portuguese (Brazil), Italian, Polish, Simplified Chinese, and Japanese.

## Why not just "a clone of ICE"

ICE and most panorama software (Hugin, PTGui, etc.) are built for photos
taken with a camera: there's perspective, lens distortion, parallax — so
these programs fit a full homography between frames (a projective
transform with "8 degrees of freedom") that can "tilt" an image like a
trapezoid.

A flatbed scan is a physically different situation: the sheet lies flat
on the glass, so there's no perspective and no scale change between two
neighboring scans — only a small rotation (if the sheet wasn't placed
perfectly straight) and a translation. So flatstitch deliberately looks
for a **rigid transform** (rotation + translation, scale always = 1),
not a homography. This isn't simplification for its own sake: the same
number of matched feature points gives a much more robust estimate when
the model has fewer degrees of freedom, and the result geometrically
cannot "skew" straight lines and text — which is exactly what most often
ruins attempts to stitch scans with general-purpose panorama software.

## Installing the .deb package (recommended)

The simplest way on Linux Mint / Ubuntu is to install the prebuilt
`flatstitch_1.0.0-1_all.deb` package:

```bash
sudo apt install ./flatstitch_1.0.0-1_all.deb
```

This puts `flatstitch` (CLI) and `flatstitch-gui` (GUI) on your PATH,
adds the app to your application menu (with an icon), and
**automatically** creates a separate, isolated Python environment in
`/opt/flatstitch/venv` and installs everything it needs there (OpenCV,
NumPy, SciPy, etc.) — your system Python is never touched. This step
needs internet access and can take a few minutes.

**If `apt install ./file.deb` reports it can't read the file** (a
notice like `_apt` couldn't access it) — this happens because home
directories are typically not readable by apt's unprivileged download
user. It's usually harmless (apt falls back to reading it as root), but
if the install genuinely fails, the most reliable fix is to install
directly with dpkg instead:

```bash
sudo dpkg -i ./flatstitch_1.0.0-1_all.deb
sudo apt-get install -f -y   # pulls in any missing dependencies
```

**If you're behind a proxy** and dependency installation fails (you'll
get an explicit message after the package installs — the package itself
still installs successfully, it just won't work yet) — `postinst` first
tries to pick up a proxy from apt's own configuration
(`Acquire::http::Proxy`); if there isn't one, it prints a ready-to-run
command. Either way, a proxy from your normal shell/`~/.bashrc` won't be
visible here: `sudo`/`apt` run postinst scripts in a minimal, isolated
environment, and even `sudo -E` doesn't change that. The manual command:

```bash
sudo http_proxy=http://HOST:PORT https_proxy=http://HOST:PORT \
    /opt/flatstitch/venv/bin/pip install -r /opt/flatstitch/requirements.txt
```

Uninstalling (cleanly removes both the venv and the package):

```bash
sudo apt remove flatstitch
```

**About the menu icon**: right after installing, some desktop shells
(Cinnamon included) only pick up a brand-new application entry — icon
included — on their next restart or login; this is normal desktop-shell
behavior, not a sign anything went wrong. If it still shows a generic
icon after logging back in, run `sudo update-icon-caches
/usr/share/icons/hicolor` (or log out/in once more).

### Building the .deb from source

If you change the code and want to rebuild the package, the layout for
`dpkg-deb` lives in `deb/flatstitch_1.0.0-1_all/`. After making edits:

```bash
sudo apt install fakeroot        # once, if you don't have it
cd deb
fakeroot dpkg-deb --build --root-owner-group flatstitch_1.0.0-1_all
```

## Installing from source (without the .deb)

If you'd rather not install system-wide, the unpacked `flatstitch/`
folder from this repo runs entirely on its own through the wrapper
scripts below, no dpkg/root needed.

Requires Python 3.9+.

```bash
cd flatstitch
pip install -r requirements.txt
# or, if the system blocks the system-wide pip:
pip install --break-system-packages -r requirements.txt
```

The GUI additionally needs the system Tk package (not installable via
pip):

```bash
sudo apt install python3-tk
```

Drag-and-drop in the GUI is optional (the `tkinterdnd2` package from
requirements.txt). Without it the GUI still works fully, just without
drag-and-drop — add files with the "Add files..." button instead.

## Quick start

`flatstitch.sh` can be called from any directory — input/output paths
are resolved relative to wherever you currently are, not the tool's own
folder:

```bash
/path/to/flatstitch/flatstitch.sh scan1.png scan2.png scan3.png

# or pass an entire folder of scans:
/path/to/flatstitch/flatstitch.sh ./scans/
```

**Where the result is saved:** if `-o` isn't given, the file is saved
automatically as `stitched.tiff` in the same folder as the first input
scan (or `stitched_2.tiff`, `stitched_3.tiff`, ... if that name is
already taken — an existing file is never silently overwritten). To set
the path yourself:

```bash
/path/to/flatstitch/flatstitch.sh ./scans/ -o /other/path/result.tiff
```

In the GUI, the "Save as" field is filled in automatically with the
same path as soon as you add the first scans, and you're free to edit it
or pick a different one via "Browse...".

If you run it without the wrapper script, via `python3 -m`, you need to
be inside the `flatstitch` folder itself (the one containing
`requirements.txt`):

```bash
cd flatstitch
python3 -m flatstitch.cli ./scans/
```

The order of files on the command line **doesn't matter** — the tool
figures out on its own which scans border which, from shared features
(lines, text, image detail), the same way ICE does. This works for a
single row of scans, a grid (rows×columns), or even an arbitrary,
unknown-in-advance layout.

## How it works (briefly)

1. **Feature search** — SIFT (default) or ORB on each scan.
2. **Matching every pair of scans** — for each pair, shared points are
   found, and a rigid transform (rotation+translation) is estimated via
   RANSAC with adaptive early stopping (fewer iterations once a model is
   already confidently good), to discard false matches. With multiple
   CPU cores, pairs are compared in parallel (`--jobs`) - this is the
   part that grows as n² with scan count, so parallelism helps most as
   the number of scans grows.
3. **Building a shared layout** — the reliable pairs are used to build
   an initial "tree" placing every scan in shared coordinates, and then
   (with 3+ linked scans) a global refinement (bundle adjustment) runs:
   all scans are simultaneously nudged so the total error across *every*
   found pair (not just the "tree") is minimized. This removes the error
   accumulation that otherwise builds up when there are many scans
   chained together.
4. **Streaming projection onto the canvas and blending** — each scan is
   read from disk one at a time, warped onto *its own* small area of the
   shared canvas (`warpAffine` with cubic interpolation — never any
   projective distortion), and immediately added into two running
   accumulators, then freed from memory - at any moment, at most one
   scan plus the canvas itself is held in memory, not one canvas-sized
   copy per scan (see "Performance" below for why this matters). In
   overlap zones, pixels are blended with a weight based on distance to
   the scan's own edge, sharpened toward a nearly "hard" seam — this
   keeps lines and text sharp, unlike wide feathering or multi-band
   blending, which visibly blur thin lines/text.
5. **Cropping and saving** — the canvas is cropped to the area that's
   actually filled (empty margins from a slight rotation are trimmed),
   and the result is saved as TIFF (LZW by default, lossless; large
   results are automatically saved as BigTIFF).

## Performance and memory

This isn't the first version of this section — it now reflects what
changed after an external code review of the first version, and why.

- **Memory no longer grows with the number of scans.** The first version
  warped EVERY scan onto a FULL-size canvas before blending - for a
  15000×15000 canvas that's ~645MB per scan, and for 50 scans that's
  30+GB just for intermediate copies. Now each scan is warped only into
  its own (much smaller) area and immediately added to a shared
  accumulator - memory depends on canvas size plus the size of ONE scan,
  not how many there are. In a test with 16 scans / a ~5400×4000 canvas,
  peak usage was **~1.2 GB** (measured with `/usr/bin/time -v`).
  Accumulators were also switched from float64 to float32 - half the
  memory, with far more precision than 8/16-bit scans need.
- **Not all originals are held in memory at once.** Each scan is loaded
  from disk for feature search, then freed; for the actual stitching it's
  loaded again, one at a time. That's one extra disk read per scan,
  which for ordinary scan files is milliseconds — far cheaper than the
  risk of running out of memory.
- **Matching scan pairs can be parallelized** (`--jobs N`, GUI:
  "Parallel processes"). Uses all CPU cores by default. This is exactly
  the part that grows as n² with scan count, so the effect is more
  noticeable for larger sets; for 2-3 scans (1-3 pairs) the tool simply
  runs them sequentially in the same process, with no per-process
  startup overhead.
- **RANSAC stops early when it's safe to** (the standard adaptive
  formula based on the current inlier ratio), instead of always running
  a fixed 3000 iterations. Together with the interpolation change
  (below), this gave ~20% speedup in testing even with no parallelism at
  all.
- **Interpolation defaults to `cubic`, not `lanczos4`.** Lanczos is
  sharper on photographs, but its wide kernel produces a noticeable
  "ringing" effect - faint halos around sharp edges, which on scans of
  text and line drawings looks like faint shadows around letters and
  lines. Cubic keeps nearly the same sharpness with much less ringing.
  `--interpolation lanczos4/linear/nearest` if you want a different
  trade-off.

**Deliberately not done (yet):**
- *Compiling RANSAC with numba (`@njit`)* would give extra speedup for a
  single pair, but numba is a heavy, finicky-to-install dependency
  (specific llvmlite versions, slow first-run compilation) for a tool
  meant to stay a simple `pip install`. Parallelism across pairs (above)
  gives a gain of the same order without that cost.
- *A sparse Jacobian (`jac_sparsity`) for bundle adjustment on hundreds
  of scans.* Worth a correction here: the suggestion this came from
  referenced a parameter called `sparse_jacobian=True`, which doesn't
  actually exist — the real `scipy.optimize.least_squares` parameter is
  called `jac_sparsity`, and it expects an actual sparsity pattern that
  has to be built correctly (which error affects which parameters). A
  mistake in that pattern isn't just a performance issue but a risk of a
  silently wrong result, so without thorough testing at that scale this
  was deliberately left out for now. For a realistic number of scans
  (single digits to a few dozen) the dense Jacobian is already fast
  enough.

## Tips for a good result

- **15-30% overlap** between neighboring scans — same as ICE/panorama
  photography. Less than that, and there may not be enough shared
  features.
- **The overlap area needs detail** (lines, text, small objects). A
  blank, featureless area simply cannot be matched, in principle - this
  applies to any feature-based tool, not just this one.
- A large random rotation of the sheet on the glass (in testing, already
  visible from ±2°) leaves noticeable white "wedges" at the edges of the
  stitched result (the tool will warn about this in its output). With
  careful scanning (rotation under ~1°), this isn't noticeable.

## Main options

| Option | Purpose |
|---|---|
| `-o, --output` | path to the output TIFF. Optional - defaults to `stitched.tiff` in the first input's folder |
| `--detector sift\|orb` | feature detector; sift is more accurate, orb is faster |
| `--jobs N` | number of processes for parallel pairwise scan matching. Default: all CPU cores |
| `--interpolation cubic\|linear\|lanczos4\|nearest` | interpolation method when rotating scans (see "Performance" above); default `cubic` |
| `--downscale N` | shrink images by this factor only for feature search (speeds up very large scans, barely affects final stitching quality since stitching itself always happens at full resolution) |
| `--min-inliers N` | how many agreeing points are needed to consider two scans linked (default 12; lower it if the overlap area has little detail) |
| `--no-bundle-adjust` | disable global refinement (only for very large sets, if speed matters more) |
| `--background white\|black` | fill color for uncovered areas (white by default, like paper) |
| `--compression lzw\|zlib\|none` | TIFF compression |
| `--lang` | interface language; auto-detected from the system locale by default |
| `-v` | verbose output (what matched what, how many inliers) |

Full list: `./flatstitch.sh --help`

## If something goes wrong

- **"No pair of images could be matched"** — check that neighboring
  scans really do overlap and that the overlap area has detail; try
  lowering `--min-inliers`.
- **"The images form N disconnected groups"** — some scans didn't find
  shared features with the rest (not enough overlap, or no shared
  detail); each group is saved as a separate file (`_part1.tiff`,
  `_part2.tiff`, ...) so you don't lose the parts that did work.
- **Visible white wedges at the edges** — increase the overlap, or place
  the sheet on the glass more carefully.

## Adding a language

Translations live in `flatstitch/locales/<code>.json` — plain JSON, one
short key per UI string. To add a new language:

1. Copy `flatstitch/locales/en.json` to `<code>.json` (use the language's
   base code, e.g. `nl` for Dutch, or `pt_BR`-style for a regional
   variant).
2. Translate every value; leave the `{placeholder}` names inside strings
   unchanged (they get filled in with numbers/paths/etc. at runtime).
3. Register it in `SUPPORTED_LANGUAGES` in `flatstitch/i18n.py` (English
   and native display names), and add `Name[<code>]=`/`Comment[<code>]=`
   lines to the `.desktop` file if you're also updating the packaged
   version.
4. Sanity-check it: `python3 -c "from flatstitch import i18n; import json;
   en=json.load(open('flatstitch/locales/en.json'));
   x=json.load(open('flatstitch/locales/<code>.json'));
   print(set(en)-set(x), set(x)-set(en))"` should print two empty sets.

Right-to-left languages (Arabic, Hebrew, etc.) aren't included yet -
Tkinter has no built-in RTL layout mirroring, so a good RTL experience
needs some extra UI work beyond just translating strings. Contributions
welcome.

## Limitations / possible future work

- Built for small rotations (careful scanning). Sheets rotated by
  90°/180° need to be oriented by hand first.
- All image pairs are compared against each other (O(n²)) - with
  `--jobs` this scales fine to dozens of scans; for hundreds, a
  candidate pre-filter (to avoid checking literally every pair) would be
  worth adding.
- The seam is built via "sharpened" distance-based feathering, not a
  full optimal-seam search (graph cut) - for document scans this is
  usually enough, but for tricky cases (fine text sitting exactly on the
  overlap boundary) true seam-carving would do better.
- A sparse Jacobian for bundle adjustment on very large sets (hundreds of
  scans) - see the explanation in "Performance" above.

## License

MIT — see [LICENSE](LICENSE).
