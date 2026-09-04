# v1.0 freeze checklist — COMPLETE

**Spec v1.0 is frozen** (this checklist is retained as a historical record). Every box is done.

## Hard prerequisites (external review)
- [x] **I.E Binary sidecar layout** made normative + self-contained, with bin golden.
- [x] **`time_meta` absent semantics** defined; `validate` WARNING; `times_only/` golden.
- [x] **t0 pinned** to the effective (dangling-dropped) record set; `ModelView.t0_ns`.
- [x] **GPU rebase relocated** I.B → II.5 (MUST → SHOULD); Part I free of consumer-compute MUSTs.

## Freeze gate
- [x] **All OPEN questions closed.** OPEN-1..7 settled (OPEN-7 = redundant NAME column rejected);
      conclusions folded into Part I/II and the decisions record.
- [x] **Goldens marked normative.** `conformance/golden/README.md` declares the fixtures the
      executable definition of the format, with a change constraint (golden edit = protocol PR).
- [x] **Draft warnings removed.** Top-of-spec DRAFT banner and per-section FROZEN scaffolding
      removed; title/status → v1.0; spec file renamed `colmap4d-v1.0.md`; README draft line removed.
- [x] **Part II/III/IV completeness pass.** No TODO/placeholder; white-paper ecosystem/roadmap
      (IV.1 v2 candidates, viewer/converter lists, validate checks) carried in.
- [x] **Spec version bump.** `time_meta.colmap4d_spec` recommended value → `"1.0"`; goldens,
      converter default, schema example, README versioning table updated.
- [x] **JSON Schema** (`spec/schemas/time_meta.schema.json`) added (informative).
- [x] **Package + citation versions** → `1.0.0` (`pyproject`, `__init__.__version__`, `CITATION.cff`).
- [x] **Conformance suite green** (ruff + pytest) on the freeze commit.
- [x] **Tag `v1.0.0`** on the merged freeze commit + GitHub Release.

## Not gating (can follow v1.0, as new optional/informative work)
- Converters beyond `per_frame_colmap` (nerfstudio bidirectional, Neu3D, HyperNeRF, EuRoC/TUM).
- `groups.txt` read/write helpers + `.bin` mirror.
- Binary (`.bin`) output from converters.
- Any future field/layer follows the `points_t_method` precedent: optional, ignorable, CHANGELOG.
