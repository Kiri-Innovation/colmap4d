# v1.0 freeze checklist

Do **not** tag `v1.0` or drop the DRAFT status until every box below is checked. Tasks 1–4 of
the external review are hard prerequisites (all now merged).

## Hard prerequisites (external review, must all land)
- [x] **I.E Binary sidecar layout** made normative + self-contained, with bin golden. *(merged)*
- [x] **`time_meta` absent semantics** defined; `validate` WARNING; `times_only/` golden. *(merged)*
- [x] **t0 pinned** to the effective (dangling-dropped) record set; `ModelView.t0_ns`. *(merged)*
- [x] **GPU rebase relocated** I.B → II.5 (MUST → SHOULD); Part I free of consumer-compute MUSTs. *(merged)*

## Remaining before v1.0
- [ ] **All OPEN questions closed.** OPEN-1..6 in `docs/open-questions.md` are resolved, but
      re-read each for any residual "provisional" wording; fold conclusions into Part I/II text.
- [ ] **Goldens marked normative.** State in `conformance/golden/README.md` (and the spec) that the
      golden fixtures are the executable definition of v1.0; freeze their bytes.
- [ ] **Draft warnings removed.** Delete the top-of-spec DRAFT banner and the per-section
      "FROZEN" scaffolding once the whole document is frozen; change the title/status to `v1.0`.
      Remove the "v0.2-draft" status line from the README.
- [ ] **Part II/III/IV completeness pass.** Ensure best practices, `groups.txt` derived view, and
      ecosystem sections have no remaining TODO/placeholder.
- [ ] **Spec version bump.** `time_meta.colmap4d_spec` recommended value → `"1.0"`; update the
      README "Versioning" table and add a `v1.0` CHANGELOG entry.
- [ ] **Conformance CI green on a clean checkout** across the supported Python versions.

## Not gating (can follow v1.0)
- Converters beyond `per_frame_colmap`; `groups.txt` read/write helpers; binary converter output.
