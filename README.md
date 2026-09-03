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

Read a COLMAP model together with its time layer — **zero dependencies**:

```python
import colmap4d

mv = colmap4d.load_model_view("path/to/sparse")   # COLMAP model dir, with or without sidecars

mv.image_ids()                 # {1, 2, 3, ...}
mv.image_time(1)               # -> t_ns (int64 nanoseconds), or None if no timestamp
mv.point_time(42)              # -> t_ns, or None if the point is temporally-unbounded
mv.effective_times()           # {image_id: t_ns} for images in the model
mv.effective_points_t()        # {point3d_id: t_ns}, dangling ids already dropped
mv.sidecars.time_meta          # {"clock_domain": "utc_ntp", ...} or None
```

Just the timestamps, without reading the base model:

```python
sc = colmap4d.load_sidecars("path/to/sparse")
sc.times[image_id]             # -> t_ns
sc.point_time(point3d_id)      # -> t_ns, or None (temporally-unbounded)
```

Validate a model (graded checks, non-zero exit on ERROR):

```python
from colmap4d import validate_full
from colmap4d.validate import exit_code
problems = validate_full("path/to/sparse")     # duplicate ids = ERROR, dangling = WARNING
code = exit_code(problems, strict=False)        # 0 if clean
```

A plain COLMAP model (no sidecars) loads fine: empty times, all points temporally-unbounded —
the backward-compatibility baseline.

The reader is **standard-library only**. `colmap4d.colmap_io` parses the classic COLMAP
txt/bin base model; where `pycolmap` is installed (optional extra `pip install
'colmap4d[model]'`) it is preferred for authoritative parsing and 3.12 rigs/frames support.

## Repository layout

```
spec/          the format specification (normative surface)
src/colmap4d/  reference implementation
  sidecar.py     zero-dep read/write of times / points_t / time_meta
  colmap_io.py   zero-dep COLMAP base-model reader/writer (classic txt/bin)
  model.py       base model + sidecars join (ModelView, load_model_view, validate_full)
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
