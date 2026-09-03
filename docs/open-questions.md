# Open questions (WP0)

Tracked ambiguities. Each was surfaced by hand-authoring the conformance golden and
forcing a decision rather than letting the implementation decide silently. Provisional
choices must be revisited before the spec leaves `v0.2-draft`.

| id | question | current decision | status |
|----|----------|------------------|--------|
| OPEN-1 | How do `time_meta.devices` (string-keyed) bind to the COLMAP model? | `camera_ids: [..]` array per device object | provisional |
| OPEN-2 | What is `t0` for the rebase contract? | `min` over `times` ∪ `points_t`; not stored | settled, minor |
| OPEN-3 | Duplicate / dangling ids in a sidecar? | duplicates MUST NOT occur (reader last-wins for now); dangling keys = `validate` warning | deferred to validate.py |
| OPEN-4 | `IMAGE_ID`/`POINT3D_ID` unstable across SfM re-runs | sidecars share model-dir lifecycle, re-emitted by importer; no format change | settled |
| OPEN-5 | empty vs absent `points_t` | equivalent (all points temporally-unbounded) | settled |

## Not decided here (out of WP0 scope)
- Part II best-practice text (centroid rule, ε defaults, filename fallback).
- Part III `groups.txt` on-disk format.
- `time_convention` values beyond `mid_exposure`.
- Whether `validate` hard-fails or warns on OPEN-3 cases.
