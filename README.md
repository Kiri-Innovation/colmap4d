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

## License

[Apache-2.0](https://github.com/Kiri-Innovation/colmap4d/blob/main/LICENSE).
