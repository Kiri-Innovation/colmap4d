# Open questions (WP0)

Tracked ambiguities. Each was surfaced by hand-authoring the conformance golden and
forcing a decision rather than letting the implementation decide silently. Provisional
choices must be revisited before the spec leaves `v0.2-draft`.

| id | question | current decision | status |
|----|----------|------------------|--------|
| OPEN-1 | How do `time_meta.devices` (string-keyed) bind to the COLMAP model? | Device attribution stays OUT of the core protocol; `camera_ids` kept as optional device→[CAMERA_ID] hint (MAY, not conformance-checked), `validate` enforces one-device-per-camera; viewer may infer by filename when absent. | **settled** |
| OPEN-2 | What is `t0` for the rebase contract? | `min` over `times` ∪ `points_t`; not stored | settled, minor |
| OPEN-3 | Duplicate / dangling ids in a sidecar? | Tolerance is normative (spec I.D): writer MUST NOT duplicate, reader last-wins (opt-in strict); dangling permitted, consumer MUST ignore model-absent ids. `validate` grades duplicate=ERROR (non-zero exit), dangling=WARNING (exit 0, `--strict` promotes); dangling message names the SfM whole-model misalignment risk. | **settled** |
| OPEN-4 | `IMAGE_ID`/`POINT3D_ID` unstable across SfM re-runs | sidecars share model-dir lifecycle, re-emitted by importer; no format change | settled |
| OPEN-5 | empty vs absent `points_t` | equivalent (all points temporally-unbounded) | settled |

## Not decided here (out of WP0 scope)
- Part II best-practice text (centroid rule, ε defaults, filename fallback).
- Part III `groups.txt` on-disk format.
- `time_convention` values beyond `mid_exposure`.
- Full `validate(model_dir)` orchestration of dangling checks (needs base-model id sets via
  pycolmap in `model.py`); the graded checks and exit-code semantics already exist.
