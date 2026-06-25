# OpenComp Test Strategy

This document keeps unit and integration responsibilities explicit so cleanup
work does not push all validation into broad app-level checks.

## Unit Tests

Use unit tests for deterministic, side-effect-light logic:

- frontend helper modules such as `projectSettings.ts`, `projectFiles.ts`,
  `projectPreferences.ts`, `projectRuntime.ts`, and other pure selectors
- backend utility modules for path handling, frame math, parser logic,
  serialization, and runtime status shaping
- logic that should be understandable and debuggable without booting the app

Current frontend unit runner:

- `cd frontend && npm run test:unit`

## Integration Tests

Use integration tests when correctness depends on wiring between modules,
runtime setup, or HTTP/request boundaries:

- backend route tests
- setup/launcher tests
- graph evaluation and viewer pipeline tests
- browser/app smoke checks
- benchmark runs used as regression signals

Current common integration checks:

- `python -m pytest backend/tests/test_setup_opencomp.py -q`
- targeted backend route/runtime suites under `backend/tests/`
- isolated startup via `scripts/setup_opencomp.py --skip-install --run`
- backend viewer benchmark via `backend/scripts/benchmark_viewer_pipeline.py`

## Rule Of Thumb

If a failure should tell you "the math or helper is wrong," prefer a unit test.
If a failure should tell you "the app wiring or runtime interaction is wrong,"
prefer an integration test.
