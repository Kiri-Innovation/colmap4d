# colmap4d

[![PyPI](https://img.shields.io/pypi/v/colmap4d)](https://pypi.org/project/colmap4d/)
[![CI](https://github.com/Kiri-Innovation/colmap4d/actions/workflows/ci.yml/badge.svg)](https://github.com/Kiri-Innovation/colmap4d/actions/workflows/ci.yml)
[![spec v1.0 frozen](https://img.shields.io/badge/spec-v1.0%20frozen-blue)](https://github.com/Kiri-Innovation/colmap4d/blob/main/spec/colmap4d-v1.0.md)

**A standard COLMAP sparse model + one timestamp per image + one timestamp per point.**
Every image and every point becomes an independent sample in spacetime `(x, y, z, t)`.
Nothing else: frame sync is just timestamps being close, and any discretization is a
derived view or a render parameter, never stored data.

colmap4d adds up to three **sidecar** files to an ordinary COLMAP model and changes no
existing file — so old tools open it as a perfectly normal (static) reconstruction, while a
4D-aware viewer unlocks a free 3D view + a draggable time axis.

> Spec **v1.0 — frozen.** See [the specification](https://github.com/Kiri-Innovation/colmap4d/blob/main/spec/colmap4d-v1.0.md);
> Part I and the conformance goldens are normative, and changes follow
> [the CHANGELOG](https://github.com/Kiri-Innovation/colmap4d/blob/main/spec/CHANGELOG.md).
>
> colmap4d is an **independent, community extension** of the COLMAP output format. It is **not
> affiliated with or endorsed by** the COLMAP project; "COLMAP" is used only to name the format
> it extends.

## Which one are you?

| I want to… | go to |
|------------|-------|
| **Read** colmap4d data (the common case) | [Quick start](#read-colmap4d-data-quick-start) below — `pip install colmap4d`, three lines |
| **Produce** colmap4d data (convert / write) | [`docs/converters.md`](https://github.com/Kiri-Innovation/colmap4d/blob/main/docs/converters.md) |
| **Build a second implementation** (C++/Rust/JS) | [the spec](https://github.com/Kiri-Innovation/colmap4d/blob/main/spec/colmap4d-v1.0.md): implement Part I, then pass every fixture in [`conformance/golden/`](https://github.com/Kiri-Innovation/colmap4d/tree/main/conformance/golden) at the `v1.0` tag |
| **Contribute** (human or AI agent) | [`CONTRIBUTING.md`](https://github.com/Kiri-Innovation/colmap4d/blob/main/CONTRIBUTING.md) + [`AGENTS.md`](https://github.com/Kiri-Innovation/colmap4d/blob/main/AGENTS.md) |
| **Drive colmap4d from an AI agent** | the [colmap4d skill](https://github.com/Kiri-Innovation/colmap4d/blob/main/skills/colmap4d/SKILL.md) |

More detail: [repository layout, dev commands & versioning](https://github.com/Kiri-Innovation/colmap4d/blob/main/docs/repo-layout.md).

## Read colmap4d data (quick start)

Read a COLMAP model together with its time layer — **zero dependencies**:

```python
import colmap4d

mv = colmap4d.load_model_view("path/to/sparse")   # COLMAP model dir, with or without sidecars

mv.point_time(42)              # -> t_ns (int64 ns), or None if the point is temporally-unbounded
mv.image_time(1)               # -> t_ns, or None
mv.effective_points_t()        # {point3d_id: t_ns}, dangling ids already dropped
mv.sidecars.time_meta          # {"clock_domain": "utc_ntp", ...} or None
```

Just the timestamps, without the base model: `colmap4d.load_sidecars("path/to/sparse")`.

A plain COLMAP model (no sidecars) loads fine: empty times, all points temporally-unbounded —
the backward-compatibility baseline. The reader is standard-library only; `pip install
'colmap4d[model]'` adds pycolmap for authoritative parsing and 3.12 rigs/frames support.

Uploading time to a GPU (4DGS/NeRF)? Rebase first — raw epoch ns overflows float32; upload
`(t − t0)` seconds as float32 via `colmap4d.rebase_to_seconds_f32(t, mv.t0_ns())`.

## Optional array readers (1.0.4)

Install `colmap4d[arrays]` to read points as NumPy arrays without constructing
one Python object per point:

```python
from colmap4d.arrays import read_point_arrays, read_image_headers

ids, xyz, rgb = read_point_arrays("sparse/0/points3D.bin")
images = read_image_headers("sparse/0/images.bin")
```

XYZ retains float64 storage precision, RGB is uint8, and IDs are sorted with
last-record-wins duplicate handling. Tracks are bounds-checked but not decoded.
Uniform-track binary files use validated strided views; other files use an
offset scan. The image reader seeks over observations instead of reading their
payload. These are optional compute APIs, not a new file format or a change to
timestamp/validation semantics. The core package remains dependency-free.

## License

[Apache-2.0](https://github.com/Kiri-Innovation/colmap4d/blob/main/LICENSE).

<!-- link-audit: all README external links verified absolute (2026-09-04, PyPI render safety) -->


## On-demand preview access (1.0.5)

`colmap4d.preview` is a standard-library-only, read-only API. It accepts a bundle
root, a sparse directory, or a split cameras/points component directory. It has
no HTTP or application dependency and does not change existing model/array APIs.

```python
from colmap4d.preview import (
    inspect_components, read_camera_page, read_point_page, sample_points, preview_files,
)

sizes = inspect_components("/data/model")  # stats only; includes all images/ files
cameras = read_camera_page("/data/model", offset=0, limit=128)
page = read_point_page("/data/model", offset=1000, limit=1024)
sample = sample_points("/data/model", budget=200_000)
# sample.points: bounded list of Point3D, with track=None
# sample.times: selected ID -> exact int64 ns; missing means temporally unbounded
files = preview_files("/data/model", part="points", budget=200_000)
# files.files: dict[str, bytes] of ordinary COLMAP tables + selected time sidecar
```

Cameras and points can be read independently. Poses omit 2D observations; point
batches omit tracks. Uniform sampling selects evenly spaced **source row ranks**
(including both endpoints; a budget of one selects the middle row), preserving
source order and original integer IDs/timestamps. At most the requested number
of geometries is decoded. Unordered/duplicate timestamp rows join by ID, last wins.
Preview files are display subsets, not reconstruction exports. All source files
remain untouched. Missing sidecars remain empty/timeless; this API does not
synthesize timestamps.

There is no persistent index: variable-length BIN records require walking preceding
track-count headers, so deep pages cost O(offset + limit) header reads and a sample
costs O(source rows) header reads. Track payloads are skipped with seek. TXT uses a
count pass and a selection pass. Timestamp sidecars are streamed in bounded chunks
and only selected IDs are retained. Memory scales with the selected batch and
intrinsics, not the source point/track count; no whole-file mmap/read is used.
Partial reads are not full-model validation. Use the existing validation API when
validating a reconstruction, including unselected records.

`ComponentSizes` reports source bytes (not decoded RAM or preview transfer bytes),
separating cameras/poses, pixel files, point geometry, and point timestamps. Pixel
symlinks or stat errors produce `images_bytes=None` with `image_size_error`, allowing
applications to ask before downloading an unknown amount. Pagination returns a
source-row `next_offset`; sampled batches have no continuation cursor.
