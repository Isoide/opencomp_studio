# Backend Restructure Report

## Scope

This pass focused on backend structure around caching, preview rendering, and node registration. Frontend changes were limited to compatibility with the existing cache status payload.

## What Changed

- Moved repeated viewer and Cryptomatte preview rendering logic out of `api/routes.py` into `core/preview_renderer.py`.
- Centralized preview cache key lookup, cache hit timing, image evaluation, OCIO display transform, resize, PNG encoding, cache storage, and viewer-input warmup in one renderer module.
- Added `PreviewRequest` as the procedural request object for standard viewer previews.
- Added `GraphEvaluator.cache_snapshot()` so API routes no longer read evaluator cache dictionaries directly.
- Removed unused backend helpers that were left behind by previous iterations.
- Extended the node abstraction with `NodeDefinition`, pairing node type, label, category, inputs, outputs, and operation in one registry entry.
- Rebuilt `NODE_REGISTRY` from `NODE_DEFINITIONS`, keeping evaluator execution and the node catalog backed by one source of truth.
- Replaced the hardcoded API node catalog list with a registry-driven catalog response.

## Caching Structure After Cleanup

- `GraphEvaluator` still owns node evaluation and cache state.
- Full-resolution node image cache and viewer preview cache remain separate budgets.
- Preview rendering now goes through `render_standard_preview()` or `render_cryptomatte_preview()`.
- Cache status now goes through `GraphEvaluator.cache_snapshot()` and returns viewer cached frames, memory usage, hit/miss counts, active nodes, and timings from one backend method.

## Node System Structure After Cleanup

- Every executable node continues to implement the `NodeOperation` protocol.
- `NodeDefinition` now provides metadata around each operation.
- `NODE_DEFINITIONS` is the backend source of truth for node catalog data.
- `NODE_REGISTRY` is derived from `NODE_DEFINITIONS`, so adding future plugin/user nodes has a clearer insertion point.

## Verification

- Backend tests: `34 passed`
- Frontend build: `npm.cmd run build` passed

