# Repository layout, development & versioning

Details sunk out of the README so the front page stays a router. (Links here are relative — this
file is read in the repo, not on PyPI.)

## Repository layout

```
spec/          the format specification (normative surface) + schemas/ + CHANGELOG.md
src/colmap4d/  reference implementation
  sidecar.py     zero-dep read/write of times / points_t / time_meta
  colmap_io.py   zero-dep COLMAP base-model reader/writer (classic txt/bin)
  model.py       base model + sidecars join (ModelView, load_model_view, validate_full)
  convert/       importers: the format's "writer" side
    per_frame_colmap.py   N per-frame COLMAP dirs -> one colmap4d model
  validate.py    graded checks (duplicate=ERROR, dangling=WARNING) + exit codes
  groups.py      derived pseudo-frame grouping                 (placeholder)
conformance/   hand-authored golden models + tests (the executable spec)
scripts/       companion tools (assemble_images.py — lay out images by model NAME)
skills/        the colmap4d agent skill (SKILL.md)
docs/          open questions, converters, notes, release runbook
```

## Development

```bash
pip install ruff pytest
ruff check . && ruff format --check .
pytest                     # runs the conformance suite
```

This is the green gate for every change (see [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the
Tier 1 / Tier 2 branch-vs-PR discipline, and [`AGENTS.md`](../AGENTS.md) for the full operating
conventions).

## Versioning

Two **independent** version axes:

| axis | where | meaning |
|------|-------|---------|
| **Package version** | `pyproject.toml` (`1.0.1`) | the Python implementation — bumps for any code change (bug fixes, new converters, API tweaks). |
| **Spec version** | `spec/` title + `time_meta.colmap4d_spec` (`1.0`) | the on-disk **protocol** — bumps only on a protocol change. |

They move at different rates: the package can release many times against one frozen spec.
**This package (1.0.1) implements spec v1.0.** Protocol changes are logged in
[`spec/CHANGELOG.md`](../spec/CHANGELOG.md).

## Viewer

The interactive 4D viewer is a **separate project** (different stack, heavier build, faster release
cadence) — the `ColmapUtil` React viewer, which already opens standard COLMAP models and is being
extended for the colmap4d time axis. It will be linked here once published under the org.
