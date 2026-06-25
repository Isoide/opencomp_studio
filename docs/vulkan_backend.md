# Vulkan Backend Roadmap

## Goal

OpenComp should use Vulkan as a maximized backend compute path for preview generation, tile rendering, proxy generation, and later more of the node graph, while keeping the current WebGL viewer as the presentation surface in the first production phase.

The implementation target is:

- Windows: first-class
- Linux: first-class
- macOS/iOS: later via MoltenVK only if the maintenance cost is justified

## Current state

The repository now has these Vulkan foundations in place:

- Vulkan instance/device/queue/command-pool initialization
- supported-node planning metadata for Vulkan spans
- runtime/cache diagnostics exposed through backend metrics
- shader compiler discovery by `glslangValidator` or `glslc`
- compiled compute shaders for `Grade` and `ColorCorrect`
- native Vulkan compute dispatch for contiguous `Grade` / `ColorCorrect` spans
- native preview/proxy resize dispatch before download for Vulkan-backed viewer requests
- native `Scale` dispatch using the shared resize kernel
- single-command-buffer native span recording with explicit compute-to-compute and compute-to-host barriers
- CPU fallback execution for unsupported or mixed spans

Current truthful behavior is:

- pure `Grade` / `ColorCorrect` spans can report `gpu_kernel_mode: "native_compute"`
- pure `Grade` / `Scale` / `ColorCorrect` spans can report `gpu_kernel_mode: "native_compute"`
- proxy viewer requests can report non-zero `gpu_resize_ms` when the resize kernel runs on GPU
- mixed spans or unsupported nodes still report `gpu_kernel_mode: "cpu_fallback"`

That means native node math now exists for the first kernel pair and the proxy preview path can keep resize on GPU, but the wider graph is still mostly CPU today.

## Architecture direction

### Phase 1: backend-first Vulkan

Keep these on CPU:

- graph orchestration
- expression evaluation
- EXR decode
- unsupported nodes
- metadata handling

Move these toward Vulkan:

- contiguous `Grade` / `ColorCorrect` / `Transform` / `Scale` / `Reformat` spans
- proxy/downscale generation
- ROI rendering
- tile rendering
- intermediate image copies and format conversion

### Phase 2: tile-native GPU preview

The viewer should keep receiving the same float-frame protocol, but the backend should produce those frames from:

- GPU span evaluation
- GPU proxy generation
- GPU tile extraction
- one download at the end of the span

### Phase 3: wider node coverage

Expand Vulkan coverage in this order:

1. `Grade`
2. `ColorCorrect`
3. `Scale`
4. `Transform`
5. `Reformat`
6. simple merge/composite kernels
7. blur and resampling-heavy nodes
8. selected mask/channel utility nodes

`HueCorrect`, `Tracker`, `Roto`, and OCIO-on-Vulkan should stay out of the first delivery batch.

## Shader strategy

### Preferred build/distribution model

Use source-controlled GLSL and distribute compiled SPIR-V artifacts with OpenComp releases.

This is better than compiling at app startup because:

- startup stays deterministic
- user machines do not need the shader compiler toolchain
- compiled assets can be validated in CI
- support/debug is easier because runtime assets are known

### Development workflow

Developers compile shaders locally with:

```bash
python backend/scripts/build_vulkan_shaders.py --check
python backend/scripts/build_vulkan_shaders.py
```

The compiler is discovered in this order:

1. `--compiler`
2. `OPENCOMP_VULKAN_SHADER_COMPILER`
3. `VULKAN_SDK/Bin`
4. `glslangValidator` / `glslang` / `glslc` on `PATH`
5. `OPENCOMP_VULKAN_SHADER_SEARCH_ROOTS`

`OPENCOMP_VULKAN_SHADER_SEARCH_ROOTS` may contain one or more fallback roots
separated by the platform path separator. It exists so host-specific fallback
locations stay configurable instead of being copied into multiple callsites.

### Runtime contract

The Vulkan runtime should refuse native compute mode unless all of these are true:

- Vulkan bindings are available
- a device/queue/command-pool is initialized
- compiled shader manifest is present and valid
- required kernels are bound successfully

That is why the runtime now tracks both:

- `native_execution_ready`
- `native_kernels_bound`

## Shader compiler installation

### Windows

Install either:

- Vulkan SDK with `glslangValidator`
- `glslc` from a shader toolchain package

Then make the executable visible on `PATH`, or set:

```powershell
$env:OPENCOMP_VULKAN_SHADER_COMPILER="C:\\Path\\To\\glslangValidator.exe"
```

### Linux

Install either:

- `glslang-tools`
- `shaderc`

Then ensure `glslangValidator` or `glslc` is on `PATH`.

### macOS / iOS

Not a delivery requirement for now. If this becomes a product target, treat it as a separate MoltenVK-backed platform effort.

## Runtime milestones

### 1. Runtime integrity

- keep honest fallback reporting
- validate shader manifest contents against required kernels
- add descriptor-set, image-view, sampler, and command-buffer helpers
- add explicit upload/download staging allocators

### 2. First real kernels

Bind native compute for:

- `Grade`
- `ColorCorrect`

Requirements:

- parameter packing matches CPU behavior
- output matches CPU within tolerance
- one upload at span entry
- one download at span exit
- one command-buffer submit per supported span instead of one submit per node

### 3. GPU resize/proxy path

Before broader node coverage, move resize/proxy work into Vulkan so the backend stops paying CPU resize after GPU math.

That path should support:

- proxy max width / height
- full-frame resize
- ROI resize
- tile-window resize

Current status:

- proxy max width / height is now running through a native resize kernel for Vulkan-backed viewer requests
- `Scale` nodes are now using the same native resize kernel inside supported Vulkan spans
- supported native spans now record all kernels into one command buffer and submit once at span exit
- ROI-aware resize and tile-window resize are still pending

### 4. GPU span planner

The planner should:

- group contiguous supported nodes into one Vulkan span
- avoid CPU/GPU bouncing between nodes
- insert explicit upload/download boundaries only when required
- report those boundaries in diagnostics

### 5. GPU cache

Keep a dedicated Vulkan cache keyed by:

- node/signature
- frame
- ROI or tile window
- precision
- proxy/full-res mode
- relevant viewer-affecting settings

Eviction should be budget-driven, not left to the driver.

### 6. ROI-aware rendering

ROI should progressively improve through these levels:

1. current viewer-side ROI request path
2. preview-path ROI cropping
3. tile-native ROI dispatch
4. Vulkan ROI dispatch with reduced upload/download footprint

Important detail:

- ROI should save display-path work immediately
- ROI should save upstream graph work only when the node path is tile-local or GPU-tile-aware

### 7. Tile-native Vulkan

For supported graphs, render only requested tiles on GPU and return:

- float tiles
- partial updates
- lane-aware metrics

This is the main path that should eventually reduce both latency and memory pressure for large frames.

## CPU fallback rules

Fallback is not an error. It is part of the design.

Fallback must happen when:

- Vulkan init fails
- shader manifest is missing
- a required kernel is missing
- a node in the span is unsupported
- validation detects incompatible parameter/layout state

When that happens:

- the graph must still render
- metrics must clearly say `gpu_kernel_mode: "cpu_fallback"`
- no partial native-then-broken result should be sent

## Benchmarks to hit

### Correctness gates

- CPU and Vulkan outputs match within agreed tolerance for `Grade` and `ColorCorrect`
- proxy output remains visually stable across frames
- ROI output lands at the correct pixel coordinates in the viewer

### Performance gates

Benchmark each of these separately:

1. cold full-res CPU
2. cold full-res Vulkan
3. cold proxy CPU
4. cold proxy Vulkan
5. warmed stepping CPU
6. warmed stepping Vulkan
7. ROI CPU
8. ROI Vulkan
9. tile-native CPU
10. tile-native Vulkan

The first acceptable milestone is:

- Vulkan proxy path beats CPU proxy path on backend processing time
- ROI path reduces backend display cost
- warm stepping does not regress

## Viewer interaction roadmap

The viewer now has an ROI tool entry point. The intended end state is:

- drag to create a pixel-snapped rectangle
- drag the body to move it
- drag edges or corners to resize it
- keep it axis-aligned
- send ROI in viewer requests
- render only that region while active

Future follow-up work:

- optional ROI lock toggle
- keyboard nudge
- ROI preset buttons
- ROI-aware tile scheduling
- ROI-aware Vulkan dispatch

## Immediate next tasks

1. Expand ROI from preview cropping to tile/GPU-aware ROI execution
2. Add native `Transform`
3. Add native `Reformat`
4. Reduce upload/download overhead with span-local reuse and cache residency
5. Reuse GPU buffers and descriptor resources across requests instead of per-span allocation
6. Add tile-native Vulkan preview for supported graphs
7. Expand native coverage without breaking truthful fallback diagnostics

## Follow-on: OpenGL OCIO Viewer Spike

After the Vulkan backend path is in a satisfactory place, run a separate GPU-viewer spike around OpenGL-based OCIO rendering.

This is now part of the active GPU roadmap, but it stays explicitly sequenced after the current Vulkan backend stabilization work. The immediate goal is still Vulkan-first backend acceleration. The OpenGL work is a follow-on viewer and OCIO architecture spike.

Reference:

- [pyociodisplay.py](https://github.com/AcademySoftwareFoundation/OpenColorIO/blob/main/src/apps/pyociodisplay/pyociodisplay.py)

Why it is worth checking:

- it demonstrates OCIO GPU shader extraction and dynamic-property updates
- it shows a DCC-style display/view pipeline on the GPU
- it uses float texture upload plus OCIO-driven GLSL, which is relevant to OpenComp viewer architecture

Specific patterns worth reusing from that sample:

- build the viewing processor from an OCIO display/view pipeline and cache against the processor cache id
- use `GpuShaderDesc` plus `getDefaultGPUProcessor().extractGpuShaderInfo(...)` as the authoritative shader-generation path
- keep exposure and gamma as OCIO dynamic properties so viewer tweaks do not force shader regeneration
- upload the source image as float RGBA texture data and let the OCIO shader plus LUT textures own the display conversion
- mirror OCIO-provided auxiliary texture and uniform binding behavior instead of hand-authoring display math

Why it should not be ingested directly:

- it is a Qt/OpenGL desktop sample, not a browser/WebGL viewer
- its surrounding assumptions are PySide/OpenGL-widget oriented
- OpenComp currently needs patterns and shader/resource handling ideas more than a direct code transplant

What is not worth carrying over directly:

- the Qt widget lifecycle and interaction model
- the PyOpenGL-specific resource management code
- desktop-window assumptions around event routing, repaint scheduling, and native swapchain ownership

Current recommendation:

- ingest the OCIO GPU shader extraction model and texture/uniform management ideas
- do not ingest the sample as code
- treat it as a reference for a future optional native OpenGL viewer spike and for tightening the current WebGL2 viewer architecture

What to evaluate in that spike:

1. Whether OpenComp should mirror the OCIO GPU shader extraction model more closely in the viewer path
2. Whether an optional native desktop OpenGL viewer would be useful for heavy local sessions
3. Whether OCIO GPU texture/uniform handling from the sample should inform WebGL2 or future native viewer code
