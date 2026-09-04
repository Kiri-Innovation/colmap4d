# colmap4d v1.0.0

**colmap4d = a standard COLMAP sparse model + one timestamp per image + one timestamp per point.**
Every image and every point becomes an independent sample in spacetime `(x, y, z, t)`; everything
else — pseudo-frames, life-spans, fade in/out — is a derived view or a render parameter, never
stored data. Old tools open a colmap4d model as a perfectly ordinary (static) COLMAP
reconstruction; a 4D-aware tool gets a draggable time axis.

## Frozen

The **spec is frozen at v1.0.** Part I (`I.A`–`I.E`) and the conformance goldens in
`conformance/golden/` are **normative**: a reader/writer is conformant iff it satisfies Part I and
reads the goldens to their documented values. From here, the format evolves only by the
`points_t_method` precedent — **new optional fields/layers** (readers already MUST tolerate unknown
fields), each logged in [`spec/CHANGELOG.md`](https://github.com/Kiri-Innovation/colmap4d/blob/main/spec/CHANGELOG.md)
— never a change to, or removal of, an existing rule.

What's normative, in one breath: `points_t` is a partial map (missing point = present at all `t`);
timestamps are signed int64 nanoseconds with a derived, unstored `t0`; `time_meta.json` declares
the clock domain and exposure convention (and readers tolerate unknown fields); duplicate ids are
last-wins and dangling ids are ignored; and the `.bin` sidecars have a fixed little-endian layout.

## Quick start

Read a COLMAP model together with its time layer — **zero dependencies**:

```python
import colmap4d
mv = colmap4d.load_model_view("path/to/sparse")   # with or without sidecars
mv.point_time(42)   # -> t_ns (int64 ns), or None if the point is temporally-unbounded
```

`pip install colmap4d`; the base-model reader is pure Python, and `pip install 'colmap4d[model]'`
adds pycolmap for authoritative parsing.

## Acknowledgements

colmap4d is an independent, community extension built entirely on top of **COLMAP**'s sparse
model format — with deep gratitude to the COLMAP authors and community. It is not affiliated with
or endorsed by the COLMAP project; it extends their format and stays strictly backward compatible
with it. The design is documented in [`spec/colmap4d-v1.0.md`](https://github.com/Kiri-Innovation/colmap4d/blob/main/spec/colmap4d-v1.0.md).
