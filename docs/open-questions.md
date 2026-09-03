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
| OPEN-6 | per-frame→single conversion: merge/dedup cross-frame points, or keep per-frame independent? | **Default = per-frame independent, NO cross-frame dedup** (white paper §3.3: honest dense sampling; dedup fabricates correspondences COLMAP never computed). `dedup_points=True` reserved but not implemented. | **needs user ratification** |

### OPEN-6 detail (awaiting user decision)
The `per_frame_colmap` converter keeps every frame's points as independent xyzt samples
(a static corner seen in 5 frames → 5 points at 5 times). This follows the white paper's
stated behavior for per-frame import and "store observations, not conclusions". The
alternative — matching the same physical point across frames into one point with a
time range/centroid — is a lossy geometric heuristic and is deliberately NOT the default.
**Question for the author:** should cross-frame dedup ever be offered (as an opt-in), and if
so, what matching criterion? For now the converter refuses `dedup_points=True`.

## Not decided here (out of WP0 scope)
- Part III `groups.txt` on-disk format.
- `time_convention` values beyond `mid_exposure`.
- Binary (`.bin`) output from converters (currently text only).
- Converters beyond `per_frame_colmap` (nerfstudio, Neu3D, HyperNeRF).
