# colmap4d spec — changelog

Entries track the **protocol** version only (independent of the Python package version — see the
README "Versioning" section). Each entry is tagged by category:

- **clarification** — wording/structure only; no conformant behavior changes.
- **normative change** — changes an obligation or behavior (a conformant reader/writer may need updating).

## Unreleased — toward v1.0

- **[normative change]** **I.E Binary sidecar layout.** The little-endian, count-prefixed byte
  layout of `times.bin` / `points_t.bin` is now specified normatively in Part I (was only in the
  reference docstring); the conformance surface is self-contained. Added `minimal_scene_bin/` and
  `dup_ids/times.bin` goldens.
- **[normative change]** **`time_meta` absent semantics (I.C).** Defines `times` without
  `time_meta` as *undeclared relative time* (orderable/differenceable within the model; MUST NOT
  be cross-model compared or asserted to a clock domain). `validate` emits a `time_meta.absent`
  WARNING; added `times_only/` golden.
- **[normative change]** **t0 domain pinned (I.B).** `t0` is the min over the model-joined,
  dangling-dropped record set, so a dangling early timestamp cannot shift it. Added
  `ModelView.t0_ns()`; `Sidecars.t0_ns()` documented as the model-less approximation. Added
  `dangling_early/` golden.
- **[normative change, relaxation]** **GPU rebase relocated (I.B → II.5).** Part I no longer
  imposes a MUST on any consumer rendering/compute process; the rebase contract moved verbatim to
  best-practice II.5 with MUST → SHOULD. Technical content unchanged.
- **[clarification]** Reordered I.D to its numbered position (after I.C); marked I.A's per-image
  coverage note as informative.

## v0.2-draft — initial draft

- Four-layer structure: Part I protocol rules (frozen I.A–I.E), Part II best practices, Part III
  derived views, Part IV ecosystem. Frozen decisions I.A–I.D and OPEN-1..6 recorded.
