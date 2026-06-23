# Slapcomp Node Knowledgebase

## Purpose

This document captures what OpenComp would need in order to support the missing node families found in the `ZBR_101_153_0080` slapcomp references. The goal is not `.nk` import. The goal is product planning, implementation planning, and algorithm selection for future OpenComp development.

## Scope

Nodes covered here:

- `FrameHold`
- `FrameRange`
- `Retime`
- `HueCorrect`
- `Tracker`
- `ColorCorrect`
- `Roto`
- `GridWarp`
- `Keyer`
- `Defocus`
- `NL_RENDER` as a low-priority in-house pipeline node

## Priority Order

Based on the current slapcomp analysis and user guidance:

| Priority | Node | Why |
| --- | --- | --- |
| High | `FrameHold` | Core time utility, low algorithm risk, high production value |
| High | `FrameRange` | Core time utility, useful for edit segmentation and shot assembly |
| High | `Retime` | Core time utility, widely used, affects render/evaluator design |
| High | `HueCorrect` | High production value for shot finishing |
| High | `ColorCorrect` | Common color tool, needed immediately for slapcomp-style work |
| Mid-High | `Tracker` | Useful, but requires viewer tooling, persistence, and solve/export logic |
| Mid-High | `Roto` | Useful, but requires a full interactive shape system and serialized animated data |
| Mid-High | `Keyer` | Useful, moderate implementation complexity |
| Mid | `GridWarp` | Sometimes useful, high interaction complexity |
| Mid | `Defocus` | Sometimes useful, moderate render complexity |
| Low | `NL_RENDER` | Pipeline-specific, should be designed against in-house publishing/render contracts |

## Cross-Cutting Architecture Work

Several of these nodes cannot be added as isolated pixel operators. OpenComp needs a few shared systems first.

### 1. Time-aware evaluation contract

Needed by `FrameHold`, `FrameRange`, `Retime`, `Tracker`, and any future temporal nodes.

Requirements:

- Nodes must be able to request source frames different from the current output frame.
- The evaluator cache key must already include frame, but time remap nodes also need clean invalidation when frame remap parameters change.
- Frame planning should expose upstream frame demand, not only upstream node demand.
- Viewer scrubbing should remain responsive when a node depends on `N`, `N-1`, or fractional source frames.

Recommended backend additions:

- Add an optional `frame_request(frame_out) -> list[frame_in]` planning hook per node.
- Add fractional-frame support in evaluation requests for nodes like `Retime`.
- Keep `Write` renders integer-frame on output, but permit subframe sampling upstream.

### 1a. Source-frame cache strategy for time nodes

This is the most important optimization follow-up for `FrameHold`, `FrameRange`, and `Retime`.

OpenComp already has the right basic direction in the current backend:

- node-result cache keyed by node/frame/signature
- separate `source_cache` for `Read` results keyed by resolved source signature

This should be extended, not replaced.

Current implementation alignment:

- `backend/opencomp/core/evaluator.py` already de-duplicates `Read` source loads through `source_cache` and `_source_inflight`.
- `backend/opencomp/nodes/read.py` already resolves constant/offset/start mappings and before/after range behavior through `_mapped_frame()` and `_range_frame()`.
- `backend/opencomp/core/channel_demand.py` already gives a basis for channel-aware cache keys so time nodes do not accidentally over-read channels.

That means the roadmap item is not "invent time caching." It is "make frame planning and cache reuse explicit for future time-remap nodes."

#### Desired behavior

- If two downstream branches resolve to the same `Read` source frame after time remapping, the image should be loaded once and reused.
- If two branches resolve to different source frames from the same `Read`, both frames may be cached at the same time if cache budget allows.
- A time-remap node should never force a `Read` to preload or decode frames that are not actually requested.
- If `FrameHold` is downstream, the `Read` should request only the held source frame and then hit cache on repeated evaluations.

#### Example

If one `Read` feeds:

- branch A: direct at output frame `1008`
- branch B: `FrameHold(first_frame=1001)`

then the same `Read` may legitimately need:

- source frame `1008` for branch A
- source frame `1001` for branch B

Those should become two source-cache entries, not a full-sequence preload.

If both branches resolve to `1001`, then only one source decode should occur and both branches should fan out from the cached result.

#### Recommended cache key policy

The source cache key should be based on the resolved source identity, not downstream node identity. At minimum:

- resolved path / virtual source ID
- resolved source frame
- file fingerprint (`mtime`, size, or equivalent)
- read colorspace / input transform
- requested channel demand

That last item matters because RGBA-only and all-channels reads should not incorrectly alias.

#### Recommended planner behavior

For any render/viewer request, build a per-`Read` demand map:

- `read_node -> {source_frame_1, source_frame_2, ...}`

derived after all downstream time remaps are applied.

This enables:

- de-duplication of identical source frame requests
- optional targeted prefetch of only the actually needed frames
- better diagnostics for branch-heavy slapcomp graphs

#### Retime-specific optimization

`Retime` may benefit from frame-pair reuse:

- nearest mode requests one integer frame
- linear mode requests `floor(source_time)` and `ceil(source_time)`

Across adjacent output frames, these pairs overlap heavily. That means a source cache can remove a lot of redundant decode work even before any optical-flow retime exists.

#### Safe product rule

Optimize only after semantic frame resolution is correct.

Wrong-but-fast time remapping is much worse than correct-but-unoptimized time remapping in comp.

### 2. Parametric curve / animation data model

Needed by `HueCorrect`, `ColorCorrect` tone ranges, `Retime` warp curves, `Tracker` track curves, and `Roto` splines.

Recommended data types:

- `AnimatedScalar`
- `AnimatedVec2`
- `AnimatedColor`
- `ParametricCurve1D`
- `BezierShape` / `BezierPoint`

### 3. Viewer overlay editing framework

Needed by `Tracker`, `Roto`, and `GridWarp`.

Recommended capabilities:

- Overlay draw layer on top of Viewer
- Hit testing in image space
- Subpixel coordinate editing
- Per-frame keyframe creation and editing
- Marquee selection
- Transform gizmos
- Undo/redo for overlay edits

### 4. Serializable interactive scene data

Needed especially by `Roto`, `Tracker`, and `GridWarp`.

The node parameter blob is not enough on its own. These nodes need structured payloads that can round-trip between frontend and backend without loss.

Recommended persistence style:

- JSON-native scene structures stored in node params or a dedicated `scene` field
- Versioned schemas
- Stable point/shape/track IDs
- Explicit keyframe arrays instead of implicit script-like strings

## Node-by-Node Knowledgebase

## FrameHold

### What it does

Maps any output frame to a held source frame.

Natron/OpenFX reference behavior:

- `source_frame = first_frame` if `increment == 0`
- otherwise `source_frame = first_frame + increment * floor((t - first_frame) / increment)`

Reference:

- `C:\Users\mzarzycki\.apps\openfx-misc\FrameHold\FrameHold.cpp`
- `C:\Users\mzarzycki\.apps\Natron\Documentation\source\plugins\net.sf.openfx.FrameHold.rst`

### Recommended OpenComp algorithm

Pure time remap. No image math.

Implementation:

1. Resolve source frame from the formula above.
2. Request the upstream image at that source frame.
3. Return it unchanged.

### Why this is high priority

- Very common utility
- Low implementation risk
- Establishes the first correct time-remap contract in the evaluator

### Product notes

- Should evaluate as identity apart from source-frame substitution.
- Viewer should display held frames correctly while scrubbing.
- Render scheduling should prefetch the held source frame, not the current output frame.
- If multiple downstream branches hold the same upstream frame, they should all hit the same `Read` source cache entry.

## FrameRange

### What it does

Constrains a clip to a frame range and defines behavior outside that range.

Natron/OpenFX modes:

- `Original`
- `Hold`
- `Black`
- `Loop`
- `Bounce`

Reference:

- `C:\Users\mzarzycki\.apps\openfx-misc\FrameRange\FrameRange.cpp`
- `C:\Users\mzarzycki\.apps\Natron\Documentation\source\plugins\net.sf.openfx.FrameRange.rst`

### Recommended OpenComp algorithm

For output time `t`, source time is:

- inside range: `t`
- before/after with `Hold`: clamp to nearest edge
- before/after with `Loop`: modulo into range
- before/after with `Bounce`: reflect into range
- before/after with `Black`: return a generated black frame

Natron formulas worth copying:

- Loop uses wrap modulo over `[start, end]`
- Bounce uses a reflected modulo over a doubled interval

### Why this is high priority

- Needed for shot slicing, slapcomp assembly, and timeline control
- Relatively small implementation with high workflow value

### Product notes

- For `Black`, generate an image using the incoming format if available, else root format.
- Bounding box/data window should remain stable and predictable.
- `FrameRange` should participate in upstream frame planning.
- `Loop` and `Bounce` should reuse source-cache entries aggressively because repeated wrapped frames are common in slapcomp-style editorial tricks.

## Retime

### What it does

Continuously remaps time, optionally with interpolation.

Natron/OpenFX reference behavior:

- Speed is integrated from the start of the source range to current time.
- Optional warp curve remaps normalized source time.
- Filters:
  - `None`
  - `Nearest`
  - `Linear`

Reference:

- `C:\Users\mzarzycki\.apps\openfx-misc\Retime\Retime.cpp`
- `C:\Users\mzarzycki\.apps\Natron\Documentation\source\plugins\net.sf.openfx.Retime.rst`

### Recommended OpenComp algorithm

For output time `t`:

1. Compute base source time by integrating animated speed.
2. Optionally reverse input by subtracting integrated motion from clip end.
3. Optionally apply a normalized warp curve.
4. Sample source at:
   - exact fractional time for `None` if upstream supports it
   - nearest integer frame for `Nearest`
   - two neighboring frames blended linearly for `Linear`

### Brief calculation method

- Base mapping:
  - `source_time = src_start + integral(speed, src_start -> t)`
  - or reversed from `src_end`
- Warp:
  - normalize into `[0,1]` over source range
  - evaluate parametric curve
  - remap back into source-frame space
- Linear filter:
  - `mix(frame_floor, frame_ceil, frac(source_time))`

### Why this is high priority

- Time remapping is foundational
- Forces OpenComp to handle subframes and multi-frame requests correctly

### Product notes

- The backend should expose fractional frame requests even if only some nodes use them.
- For v1, linear interpolation is enough.
- Optical-flow retime should be a future separate node, not part of this one.
- For `Linear`, cache reuse of `floor(source_time)` and `ceil(source_time)` is a real optimization target because neighboring output frames often share one or both source frames.

## HueCorrect

### What it does

Applies hue-dependent adjustments using curves over hue space.

Natron/OpenFX reference:

- Curves drive:
  - hue shift
  - saturation gain
  - luminance gain
  - red/green/blue gains
  - red/green/blue suppression
  - saturation threshold

Reference:

- `C:\Users\mzarzycki\.apps\openfx-misc\HueCorrect\HueCorrect.cpp`
- `C:\Users\mzarzycki\.apps\Natron\Documentation\source\plugins\net.sf.openfx.HueCorrect.rst`

### Recommended OpenComp algorithm

Per pixel:

1. Unpremult if requested.
2. Convert RGB to HSV.
3. Evaluate all hue curves at the pixel hue.
4. Apply hue shift.
5. Apply channel suppressions.
6. Apply hue-conditioned color and luminance gains, gated by saturation threshold.
7. Apply saturation gain.
8. Optionally restore original luminance by luminance mixing.
9. Clamp if enabled.
10. Premult back and mix with original.

### Brief calculation method

- Hue shift:
  - shift hue around the circle, wrap modulo 1
- Saturation:
  - `rgb_out = lerp(luma(rgb), rgb, sat_gain)`
- Channel suppression:
  - example red:
  - if `r > min(g, b)`, replace with `min(g, b) + r_sup * (r - min(g, b))`
- Luminance mix:
  - scale output RGB so output luminance moves back toward input luminance

### Why this is high priority

- Strong finishing value
- Local algorithm, no graph-level complexity
- Good candidate for CPU SIMD or GPU later

### Product notes

- Needs a parametric curve editor in the frontend.
- Curves should be editable in a hue-wheel-friendly UI, but backend only needs curve samples/evaluation.
- Must support mask and mix.

## Tracker

### What it does

Tracks one or more 2D points across frames and then solves a transform or corner pin from those tracks.

Natron references show two tracker families:

- `TrackerPM`: older exhaustive-search pattern matcher
- built-in `Tracker`: LibMV-based multi-point tracker with transform export

References:

- `C:\Users\mzarzycki\.apps\openfx-misc\TrackerPM\TrackerPM.cpp`
- `C:\Users\mzarzycki\.apps\Natron\Documentation\source\plugins\fr.inria.built-in.Tracker.rst`
- `C:\Users\mzarzycki\.apps\Natron\libs\libmv\libmv\tracking\track_region.cc`

### Recommended OpenComp direction

Do not copy `TrackerPM` as the main product architecture.

Use a LibMV-style tracker architecture:

- track markers with subpixel positions
- support motion models:
  - translation
  - translation + scale
  - translation + rotation
  - translation + rotation + scale
  - affine
  - homography
- solve exported transforms from valid tracks at each frame

### Brief calculation method

Tracking itself:

1. Extract a pattern patch around the marker on a reference frame.
2. Search in the next frame with a chosen motion model.
3. Minimize patch error or maximize correlation.
4. Store the resulting point position as an animated curve.

Simple matcher metrics from `TrackerPM`:

- `SSD`: sum of squared differences
- `SAD`: sum of absolute differences
- `NCC`: normalized cross-correlation
- `ZNCC`: zero-mean normalized cross-correlation

LibMV-style solve:

- fit warp parameters to minimize reprojection/pixel matching error
- export either:
  - transform parameters
  - corner pin / homography

### Other tracking algorithms worth evaluating

Natron/LibMV is still a useful reference, but it should not be treated as the only option.

There are really three tracker families OpenComp could evaluate.

#### 1. Classical sparse point tracking

Best fit for:

- deterministic compositor workflows
- CPU execution
- subpixel point tracks
- transform / corner pin solve

Options:

- pyramidal Lucas-Kanade sparse optical flow
- feature detect + match + robust solve
- ECC alignment for patch/ROI alignment

References:

- OpenCV `calcOpticalFlowPyrLK`: sparse iterative Lucas-Kanade with pyramids
- OpenCV `buildOpticalFlowPyramid`: explicit pyramid construction for reuse
- OpenCV `findTransformECC`: direct image alignment for translation / Euclidean / affine / homography
- OpenCV `estimateAffinePartial2D` and `findHomography`: robust transform solve with RANSAC / LMedS

Why it matters:

- very practical for comp
- good CPU speed
- easy to make deterministic and debuggable
- straightforward to export into `Transform` / `CornerPin` style nodes

#### 2. Dense optical-flow-assisted tracking

Best fit for:

- large deformations
- difficult local motion
- motion-guided initialization for point tracks

Options:

- Farneback dense optical flow
- DIS optical flow
- RAFT-style learned optical flow

References:

- OpenCV `calcOpticalFlowFarneback`
- OpenCV `DISOpticalFlow`
- RAFT (ECCV 2020)

Why it matters:

- useful as a proposal field
- can initialize or recover point tracks after failure
- can support future warp tools

But:

- dense flow alone is not a compositor tracker UI
- it still needs point-level confidence, user correction, and export logic

#### 3. Modern learned point trackers

Best fit for:

- heavy occlusion
- long-range correspondence
- hard non-rigid motion
- optional GPU-assisted "advanced tracking" mode

Most relevant current options:

- TAPIR (ICCV 2023)
- CoTracker (ECCV 2024)
- CoTracker3 (ICCV 2025)

Why these matter:

- they track arbitrary points rather than just bounding boxes
- they explicitly address occlusion robustness
- they are much more relevant to comp-style point tracking than generic object-box trackers

Important caveat:

- these models are strong candidates for an optional advanced tracker backend, not necessarily for the first shipping tracker
- they increase runtime, packaging, GPU dependency, and determinism complexity

### Tracking methods that are less relevant as the main compositor tracker

OpenCV also exposes object trackers such as:

- `TrackerCSRT`
- `TrackerNano`
- `TrackerVit`

These are useful for tracking object boxes, but they are not the right core primitive for compositor-style point tracking and transform solving.

They are worth knowing about, but I would not build the main OpenComp tracker around them.

### Other tracker algorithms worth checking

If the goal is broader research rather than immediate implementation, these are also worth evaluating.

#### KLT / Shi-Tomasi + subpixel refinement

- Detect corners with Shi-Tomasi or Harris.
- Track with pyramidal Lucas-Kanade.
- Refine point positions with subpixel corner refinement.

Why it matters:

- still one of the best CPU baselines for a compositor tracker
- deterministic
- easy to debug when artists manually adjust tracks

#### Feature descriptor matchers

Examples:

- SIFT
- ORB
- AKAZE

Use case:

- hard cuts
- large jumps
- re-acquisition after track failure

These are not ideal as the main per-frame tracker, but they are good recovery tools.

#### Direct alignment solvers

Examples:

- ECC alignment
- inverse compositional Lucas-Kanade

Use case:

- stable textured patches
- solving translation / affine / homography directly from an image region

These are attractive for a refinement pass after a coarse track estimate.

### Why this is mid-high priority

- Production value is high
- But it is not just a node, it is a subsystem

### Product notes

- Viewer input is critical here: users place and correct tracks visually at subpixel precision.
- The frontend must support track markers, pattern box, search box, keyframes, and correction edits.
- Track edits must round-trip as structured animated data.
- Transform export should be deterministic and recomputable from track data.
- The tracker node itself should probably remain pass-through until a transform mode is enabled, matching common compositor behavior.

### Recommended phased delivery

1. Single-point tracker with translation only
2. Multi-point storage and playback
3. Transform solve from tracks
4. CornerPin/homography solve
5. Robust fitting and smoothing
6. Optional advanced tracker backend for hard shots

### Recommended tracker roadmap by backend

#### Tracker v1

- `goodFeaturesToTrack`-style corner initialization or manual point placement
- pyramidal Lucas-Kanade style local tracking
- RANSAC transform solve
- homography export when enough points exist

This is the best speed-to-value choice.

#### Tracker v2

- ECC patch alignment as a refinement pass
- dense-flow initialization for difficult motion
- confidence-based re-track / recovery

#### Tracker v3

- optional learned backend such as TAPIR or CoTracker-family
- offline and online modes
- explicit occlusion/confidence output
- GPU-required advanced mode

## ColorCorrect

### What it does

Applies saturation, contrast, gamma, gain, and offset globally and optionally per shadows/midtones/highlights ranges.

Reference:

- `C:\Users\mzarzycki\.apps\openfx-misc\ColorCorrect\ColorCorrect.cpp`
- `C:\Users\mzarzycki\.apps\Natron\Documentation\source\plugins\net.sf.openfx.ColorCorrectPlugin.rst`

### Recommended OpenComp algorithm

Per pixel:

1. Compute shadow/midtone/highlight weights from a tone-range curve.
2. Evaluate three corrected versions of the pixel:
   - shadows-adjusted
   - midtones-adjusted
   - highlights-adjusted
3. Blend them by the three weights.
4. Apply master group adjustments.
5. Apply mask and mix.

Natron/OpenFX operation order inside a group:

1. saturation
2. contrast
3. gamma
4. gain
5. offset

### Brief calculation method

- Saturation:
  - `rgb_out = lerp(luma(rgb), rgb, saturation)`
- Contrast:
  - `out = (in / 0.18) ^ contrast * 0.18`
- Gamma:
  - `out = in ^ (1 / gamma)` for positive values
- Gain:
  - `out = in * gain`
- Offset:
  - `out = in + offset`

### Why this is high priority

- Common everyday node
- Directly needed by the slapcomp references
- Implementation is straightforward once curve params exist

### Product notes

- A v1 may ship without full SMH ranges and still provide production value.
- If that happens, the node should be split into:
  - `ColorCorrect` v1 basic
  - advanced tone-range mode later

## Roto

### What it does

Creates masks and shapes using animated Bezier splines, feather, opacity, transforms, and lifetime controls.

References:

- `C:\Users\mzarzycki\.apps\Natron\Documentation\source\plugins\fr.inria.built-in.Roto.rst`
- `C:\Users\mzarzycki\.apps\Natron\Engine\Bezier.h`
- `C:\Users\mzarzycki\.apps\Natron\Engine\BezierCPSerialization.h`

### Recommended OpenComp algorithm

This should not be treated as a simple raster effect node. It is a vector scene node that rasterizes to an image or alpha output at render time.

Data model should include:

- shapes
- layers
- control points
- left/right tangent handles
- feather points or feather width
- per-shape opacity/color
- lifetime / activation
- per-frame keyframes

Rasterization pipeline:

1. Evaluate each shape at the current frame by interpolating animated control points and tangents.
2. Build shape contours.
3. Rasterize fill into a coverage mask.
4. Apply feather falloff.
5. Composite shapes/layers in hierarchy order.
6. Output alpha-only or RGBA, depending on node mode.

### Brief calculation method

- Spline evaluation:
  - cubic Bezier segments between control points
- Feather:
  - offset contour or secondary feather contour, then ramp coverage between inner and outer boundaries
- Lifetime:
  - enable shape only on matching frames or by animated activation curve

### Why this is mid-high priority

- Very useful in production
- But it needs a real authoring system, not just backend math

### Product notes

- Viewer editing is mandatory.
- Frame-by-frame and keyframed spline editing must be first-class.
- Backend should not store opaque script strings. Use structured JSON.
- Roto output should be usable both as alpha and as an image source for things like custom filters.

## GridWarp

### What it does

Warps an image by mapping one Bezier grid to another.

Foundry reference summary:

- a source grid defines where pixels come from
- a destination grid defines where pixels are moved to
- rendering subdivides the Bezier grid into a submesh and resamples the image through that deformation

References:

- Foundry GridWarp docs: https://learn.foundry.com/nuke/content/reference_guide/transform_nodes/gridwarp.html

Note:

- I did not find a direct `GridWarp` implementation in the pulled Natron / `openfx-misc` trees available locally.

### Recommended OpenComp algorithm

Represent the warp as a deforming mesh:

1. Evaluate source and destination Bezier grids at the current frame.
2. Tessellate each grid cell into a submesh.
3. Build a mapping from destination triangles back into source UV/image coordinates.
4. For each output pixel:
   - find containing destination triangle
   - compute barycentric coordinates
   - map back into source triangle
   - sample source image with selected filter

### Brief calculation method

- Grid deformation:
  - Bezier patch edges define the mesh boundaries
- Resampling:
  - inverse mapping from output pixel to source image position
- Quality:
  - submesh resolution controls how finely curved cells are approximated

### Why this is mid priority

- Useful, but not as universal as the time and color tools
- High UI and serialization complexity

### Product notes

- Viewer overlay editing is mandatory.
- The grid itself must be animatable and keyframeable.
- For v1, support one grid pair and one image input.
- Tracker-to-grid matrix linking can be a later phase.

## Keyer

### What it does

Generates a matte from image color or luminance, with optional despill and mask inputs.

References:

- `C:\Users\mzarzycki\.apps\openfx-misc\Keyer\Keyer.cpp`
- `C:\Users\mzarzycki\.apps\Natron\Documentation\source\plugins\net.sf.openfx.KeyerPlugin.rst`

### Recommended OpenComp algorithm

Per pixel:

1. Compute a foreground key value from:
   - luminance
   - color distance / color similarity
   - screen-style keyed component
2. Convert that foreground key into matte alpha using the piecewise linear tolerance/softness mapping.
3. Apply inside/outside masks and optional source alpha handling.
4. Optionally despill.
5. Return intermediate, premultiplied, unpremultiplied, or composite output.

### Brief calculation method

Natron documents the matte mapping as a piecewise linear function with four thresholds:

- below `A`: 0
- between `A` and `B`: ramp to 1
- between `B` and `C`: 1
- between `C` and `D`: ramp back to 0
- above `D`: 0

Where:

- `A = center + toleranceLower + softnessLower`
- `B = center + toleranceLower`
- `C = center + toleranceUpper`
- `D = center + toleranceUpper + softnessUpper`

### Why this is mid-high priority

- Useful node with moderate complexity
- Strong complement to `Roto`

### Product notes

- Use inside/outside matte inputs from day one.
- Support multiple output modes, especially intermediate and unpremultiplied.
- Despill can ship in a simplified form if needed.

## Defocus

### What it does

Applies lens-like defocus blur with a disc-shaped or aperture-shaped kernel.

Foundry references:

- `Defocus` uses a disc filter for circular lens blur
- `ZDefocus` performs depth-based layer splitting and back-to-front compositing

References:

- Defocus docs: https://learn.foundry.com/nuke/content/reference_guide/filter_nodes/defocus.html
- ZDefocus docs: https://learn.foundry.com/nuke/content/reference_guide/filter_nodes/zdefocus.html

### Recommended OpenComp direction

Implement plain `Defocus` first, not depth-aware `ZDefocus`.

### Recommended OpenComp algorithm

Two acceptable implementations:

1. Disc-kernel spatial convolution for small radii
2. FFT-based convolution for larger radii or custom aperture kernels

For v1:

- support scalar radius
- support aspect ratio / scale
- support mask and mix

### Brief calculation method

- Convolve the image with a normalized disc or aperture kernel.
- If using brighter highlights and larger kernels, preserve HDR values in float.
- For aperture shapes beyond a disc, a generic convolution path is preferable.

### Why this is mid priority

- Useful, but simpler blur nodes cover many cases
- Real value comes when bokeh quality is good

### Product notes

- Keep RGBA float internally.
- Avoid converting to 8-bit or premultiplying incorrectly around highlights.
- Depth-aware defocus should be a separate future node.

## Other Algorithms Worth Evaluating

Natron is a good reference for feature parity and expected user behavior, but not always the best implementation target. The following alternatives are worth keeping in the research backlog.

### Time nodes

For `FrameHold`, `FrameRange`, and `Retime`:

- frame-demand planning per `Read`
- source-frame pair reuse for linear interpolation
- optional small per-node remap lookup tables for long retimes or dense speed curves

The core idea is to make frame resolution cheap before any image evaluation starts.

### ColorCorrect / HueCorrect

Possible future alternatives:

- operate in perceptual spaces such as OKLab/OKLCh for some color tools
- GPU LUT approximation for curve-heavy interactive previews
- masked / region-limited evaluation for viewer responsiveness

For now, Natron-style float RGB processing is the correct baseline.

### Roto

Possible rasterization backends:

- scanline polygon fill with analytic edge coverage
- signed-distance-field acceleration for previews
- tile-aware rasterization so only visible regions are rebuilt while editing

The shipping backend should still preserve exact spline data, not only rasterized outputs.

### GridWarp

Alternative warp formulations worth researching:

- triangle mesh warp with barycentric resampling
- moving least squares image deformation
- thin-plate spline warp for sparse-control deformation

For Nuke-style `GridWarp`, mesh-based deformation is the best semantic match. MLS and TPS are still useful reference algorithms for future deformation tools.

### Keyer

Alternative matte-generation families worth researching:

- color-difference keying
- similarity / distance keying in normalized color space
- edge-aware matte refinement
- Bayesian or sample-based keying approaches for harder blue/green screen shots

For OpenComp v1, a deterministic tolerance/softness keyer with despill is the right starting point.

### Defocus

Alternative blur backends worth researching:

- summed-area or mip-based approximations for fast preview
- FFT convolution for large kernels
- polygon/aperture convolution for high-quality bokeh
- scatter-as-gather depth compositing for future `ZDefocus`

The most important product split is still:

- `Defocus`: image blur with lens-like kernel
- `ZDefocus`: separate future depth-aware node

## External References

- Foundry GridWarp docs: <https://learn.foundry.com/nuke/content/reference_guide/transform_nodes/gridwarp.html>
- Foundry Defocus docs: <https://learn.foundry.com/nuke/content/reference_guide/filter_nodes/defocus.html>
- Foundry ZDefocus docs: <https://learn.foundry.com/nuke/content/reference_guide/filter_nodes/zdefocus.html>
- OpenCV video tracking docs: <https://docs.opencv.org/4.x/dc/d6b/group__video__track.html>
- OpenCV `TrackerCSRT`: <https://docs.opencv.org/4.x/d2/da2/classcv_1_1TrackerCSRT.html>
- OpenCV calib3d / homography docs: <https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html>
- TAPIR project: <https://deepmind-tapir.github.io/>
- CoTracker project: <https://co-tracker.github.io/>
- CoTracker3 project: <https://cotracker3.github.io/>

## NL_RENDER

### What it does

Pipeline-specific publish/render/write helper.

### Recommended direction

Do not implement as a generic image node first.

Treat it as:

- metadata + path templating
- show/shot/task/version resolution
- render contract integration
- optional burn-in/preview side effects

### Why low priority

- In-house specific
- Better designed once the rest of the render/publish contract is stable

## Recommended Delivery Sequence

### Phase 1: core time and color

- `FrameHold`
- `FrameRange`
- `Retime`
- `ColorCorrect` basic
- `HueCorrect`

### Phase 2: keying and masks

- `Keyer`
- `Roto` alpha-only first

### Phase 3: tracking and transform export

- `Tracker` with structured track data
- export to `Transform` and homography-style node data

### Phase 4: deformation and lens finishing

- `GridWarp`
- `Defocus`

### Phase 5: pipeline features

- `NL_RENDER`

## Suggested OpenComp Internal Schemas

### Tracker schema sketch

```json
{
  "tracks": [
    {
      "id": "track_01",
      "label": "track 1",
      "enabled": true,
      "reference_frame": 1001,
      "pattern_box": {"x": 10.5, "y": 20.5, "w": 31.0, "h": 31.0},
      "search_box": {"x": 0.0, "y": 0.0, "w": 61.0, "h": 61.0},
      "points": [
        {"frame": 1001, "x": 1234.25, "y": 876.5, "confidence": 1.0},
        {"frame": 1002, "x": 1235.02, "y": 877.1, "confidence": 0.96}
      ]
    }
  ]
}
```

### Roto schema sketch

```json
{
  "shapes": [
    {
      "id": "shape_01",
      "type": "bezier_closed",
      "opacity": 1.0,
      "feather": 6.0,
      "activated": true,
      "lifetime": {"mode": "all"},
      "keyframes": [
        {
          "frame": 1001,
          "points": [
            {"x": 100.0, "y": 200.0, "in": [90.0, 200.0], "out": [110.0, 200.0]},
            {"x": 150.0, "y": 240.0, "in": [145.0, 230.0], "out": [160.0, 250.0]}
          ]
        }
      ]
    }
  ]
}
```

## Main Takeaways

- `FrameHold`, `FrameRange`, `Retime`, `ColorCorrect`, and `HueCorrect` should come first because they bring high production value without requiring a full interactive overlay authoring stack.
- `Tracker`, `Roto`, and `GridWarp` are product features as much as they are node implementations.
- `Tracker` and `Roto` must be designed around viewer interaction, subpixel edits, keyframing, and structured serialization.
- `GridWarp` should be implemented as animated mesh deformation, not as a generic transform shortcut.
- `Defocus` should start as true aperture/disc convolution; depth-aware defocus should remain a separate node.
- `NL_RENDER` should stay low priority and pipeline-specific.
