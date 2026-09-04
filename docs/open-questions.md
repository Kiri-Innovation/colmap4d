# Open questions (WP0)

Tracked ambiguities. Each was surfaced by hand-authoring the conformance golden and
forcing a decision rather than letting the implementation decide silently. **OPEN-1..6 are all
settled** — the normative outcomes live in the spec (Part I/II); this file is a provenance
record. Nothing below is provisional.

| id | question | current decision | status |
|----|----------|------------------|--------|
| OPEN-1 | How do `time_meta.devices` (string-keyed) bind to the COLMAP model? | Device attribution stays OUT of the core protocol; `camera_ids` kept as optional device→[CAMERA_ID] hint (MAY, not conformance-checked), `validate` enforces one-device-per-camera; viewer may infer by filename when absent. | **settled** |
| OPEN-2 | What is `t0` for the rebase contract? | `min` over the model's **effective** records (`times` ∪ `points_t` after joining + dropping dangling ids, spec I.B); `ModelView.t0_ns()` computes it, `Sidecars.t0_ns()` is the model-less approximation; not stored | **settled** |
| OPEN-3 | Duplicate / dangling ids in a sidecar? | Tolerance is normative (spec I.D): writer MUST NOT duplicate, reader last-wins (opt-in strict); dangling permitted, consumer MUST ignore model-absent ids. `validate` grades duplicate=ERROR (non-zero exit), dangling=WARNING (exit 0, `--strict` promotes); dangling message names the SfM whole-model misalignment risk. | **settled** |
| OPEN-4 | `IMAGE_ID`/`POINT3D_ID` unstable across SfM re-runs | sidecars share model-dir lifecycle, re-emitted by importer; no format change | settled |
| OPEN-5 | empty vs absent `points_t` | equivalent (all points temporally-unbounded) | settled |
| OPEN-6 | per-frame→single conversion: merge/dedup cross-frame points, or keep per-frame independent? | **Settled (author, per white paper Q3/§3.3): keep per-frame independent, NO cross-frame dedup.** Static structure is honestly sampled once per instant; cross-frame dedup is a derived view / downstream optimization, out of scope for v1. `dedup_points=True` stays reserved and refused. | **settled** |
| OPEN-7 | Add a redundant `NAME` column to the `times` sidecar? | **Rejected** (see detail). | **rejected-unless-new-evidence** |

### OPEN-6 detail (settled)
The `per_frame_colmap` converter keeps every frame's points as independent xyzt samples
(a static corner seen in 5 frames → 5 points at 5 times). White paper Q3/§3.3: this is the
honest dense sampling, and it is `N + const` in storage because within-frame duplication is
already deduped by the track structure — per-frame COLMAP is `N × T` only if you keep whole
frames, which is the *source* form, not ours. Cross-frame dedup (matching the same physical
point across frames into one point with a time range) is a lossy geometric heuristic and a
**derived view / downstream optimization**, explicitly out of scope for v1. `dedup_points=True`
remains reserved and is refused by the converter with a pointer to this decision.

## Resolved since (now implemented)
- Part II best practices, Part III `groups.txt` on-disk format, Part IV ecosystem — drafted.
- Full `validate` orchestration of dangling checks — `model.validate_full` reads model id sets
  with the zero-dep `colmap_io` reader (no pycolmap required).
- Base-model reading: refined the earlier "delegate to pycolmap only" stance — added a
  zero-dependency pure-Python classic txt/bin reader (`colmap_io`) as the default, with pycolmap
  preferred where installed. Rationale: `import colmap4d` should read a timestamped model with
  no compiled dependency; pycolmap stays authoritative for 3.12 rigs/frames binary variants.

### OPEN-7 detail — redundant `NAME` column in `times` (rejected-unless-new-evidence)

A `times` sidecar with `IMAGE_ID  NAME  T_NS` instead of `IMAGE_ID  T_NS` was proposed and
**rejected**. Reasons (kept so this is not silently re-opened):
- `NAME` already lives in the base model's `images` (`IMAGE_ID → NAME` is COLMAP's own mapping);
  putting it in `times` is pure redundancy — the same string is one join away.
- The only scenario it helps is a sidecar traveling **detached from its model** — which directly
  violates the settled OPEN-4 rule that sidecars share the model directory's lifecycle. We should
  not design for a state we've declared invalid.
- Redundancy introduces a new inconsistency class (NAME vs IMAGE_ID pointing at different images —
  who wins?), re-opening the OPEN-3 "two sources of truth" battle on a second front.

Reconsider only if concrete new evidence appears that sidecars must circulate detached from
models (a real interchange need OPEN-4 did not anticipate).

## Not decided here (out of WP0 scope)
- `time_convention` values beyond `mid_exposure` — **confirmed v2** (a new value is a protocol
  change; the v1 surface fixes it to `mid_exposure`).
- Binary (`.bin`) output from converters (currently text only).
- `groups.txt` read/write helpers + `.bin` mirror (format is specified; I/O not yet coded).
- Converters beyond `per_frame_colmap` (nerfstudio, Neu3D, HyperNeRF).
