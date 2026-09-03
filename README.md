# colmap4d

**A standard COLMAP sparse model + one timestamp per image + one timestamp per point.**
Every image and every point becomes an independent sample in spacetime `(x, y, z, t)`.
Nothing else: frame sync is just timestamps being close, and any discretization is a
derived view or a render parameter, never stored data.

colmap4d adds up to three **sidecar** files to an ordinary COLMAP model and changes no
existing file — so old tools open it as a perfectly normal (static) reconstruction, while a
4D-aware viewer unlocks a free 3D view + a draggable time axis.

> Status: **v0.2-draft** — spec not yet frozen. See [`spec/colmap4d-v0.2-draft.md`](spec/colmap4d-v0.2-draft.md).
> Only the three Part I rules marked FROZEN are settled enough to build against.

## 30-second quickstart

```python
from colmap4d import load_sidecars

sc = load_sidecars("path/to/sparse")     # a COLMAP model dir, with or without sidecars
sc.times[image_id]                        # -> t_ns (int64 nanoseconds)
sc.point_time(point3d_id)                 # -> t_ns, or None if temporally-unbounded
sc.time_meta["clock_domain"]              # -> "utc_ntp", ...
```

A plain COLMAP model (no sidecars) loads fine and reports empty times / all points
temporally-unbounded — that is the backward-compatibility baseline.

The core (`colmap4d.sidecar`) is **standard-library only, zero dependencies**. Parsing the
COLMAP base model itself is delegated to `pycolmap` (optional extra, `pip install
colmap4d[model]`) — colmap4d never reimplements COLMAP's own parsers.

## Repository layout

```
spec/          the format specification (normative surface)
src/colmap4d/  reference implementation
  sidecar.py     zero-dep read/write of times / points_t / time_meta
  model.py       base model via pycolmap + sidecars (ModelView, validate_full)
  convert/       importers: the format's "writer" side
    per_frame_colmap.py   N per-frame COLMAP dirs -> one colmap4d model
  validate.py    graded checks (duplicate=ERROR, dangling=WARNING) + exit codes
  groups.py      derived pseudo-frame grouping                 (placeholder)
conformance/   hand-authored golden models + tests (the executable spec)
docs/          open questions, notes
```

## Viewer

The interactive 4D viewer is a **separate project** (different stack, heavier build,
faster release cadence). Today that is the `ColmapUtil` React viewer living alongside this
repo (`../ColmapUtil`); it already opens standard COLMAP models and is being extended for
the colmap4d time axis. It will be linked here once published under the org.

## Development

```bash
pip install ruff pytest
ruff check . && ruff format --check .
pytest                     # runs the conformance suite
```

## License

[Apache-2.0](LICENSE).
