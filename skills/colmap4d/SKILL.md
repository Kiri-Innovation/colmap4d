---
name: colmap4d
description: >-
  Read, write, or independently implement colmap4d — a standard COLMAP sparse model plus one
  timestamp per image and per point (x, y, z, t). Use when working with times.txt / points_t /
  time_meta sidecars, "4D COLMAP", dynamic-scene / multi-view timestamps, per-frame COLMAP
  conversion, or loading timestamped sparse points as a seed for 4DGS / NeRF / dynamic reconstruction.
---

# colmap4d

**colmap4d = a standard COLMAP sparse model + one timestamp per image + one timestamp per point.**
Every image and point is an independent sample in spacetime `(x, y, z, t)`; everything else —
pseudo-frames, life-spans, fade in/out — is a derived view or a render parameter, never stored.
It is three optional **sidecar** files added next to `cameras/images/points3D`, changing no existing
file, so old COLMAP tools still open it as an ordinary (static) model.

## Hard semantics (get these right)

- **int64 nanoseconds, never float seconds.** Timestamps (`T_NS`) are signed int64 ns. `float64`
  seconds loses sub-ms precision at epoch scale — never store or round-trip through it.
- **`points_t` is a partial map; missing = temporally-unbounded.** A point with no `points_t`
  entry (or an absent/empty `points_t`) has **no timestamp** — present at all `t`. Represent it as
  `None`/null, never `0`. Same for images absent from `times`.
- **Duplicate id → last-wins; dangling id → ignore.** A repeated id resolves to its last occurrence;
  a sidecar id not in the base model is dropped by a model-aware consumer.
- **`.bin` beats `.txt`** when both are present. `time_meta` is JSON only.

> **This skill corresponds to spec v1.0 and is a derived view of it, not a second normative source.**
> If anything here conflicts with `spec/colmap4d-v1.0.md`, the spec wins.

## Where to look

| need | go to |
|------|-------|
| the normative rule for X | `spec/colmap4d-v1.0.md` Part I (I.A partial map, I.B int64/t0, I.C time_meta, I.D dup/dangling, I.E binary) |
| a worked example / exact bytes | `conformance/golden/` (`minimal_scene`, `minimal_scene_bin`, `times_only`, `dangling_early`, …) |
| what changed / how it evolves | `spec/CHANGELOG.md` |
| is there already a converter | `src/colmap4d/convert/` + spec Part IV |

---

## If you are a CONSUMER (reading colmap4d data — the common case)

Use the Python package (`pip install colmap4d`; base-model reader is pure-Python zero-dep,
`pip install 'colmap4d[model]'` adds pycolmap):

```python
import colmap4d

mv = colmap4d.load_model_view("path/to/sparse")   # works with OR without sidecars
mv.image_ids(); mv.point_ids()
mv.point_time(pid)        # -> int ns, or None (temporally-unbounded — handle it, don't treat as 0)
mv.image_time(iid)        # -> int ns, or None
mv.effective_times()      # {image_id: t_ns}   (dangling ids already dropped)
mv.effective_points_t()   # {point3d_id: t_ns}
mv.sidecars.time_meta     # {"clock_domain": ..., ...} or None
```

A no-sidecar (plain COLMAP) model loads fine: empty times, all points temporally-unbounded.

**Feeding a GPU / 4DGS / NeRF loader — rebase first.** Raw epoch ns overflows float32; compute the
model origin once and upload `(t − t0)` seconds as float32 (a sentinel, e.g. `-1`, for timeless):

```python
t0 = mv.t0_ns()                         # min over effective records; None if no timestamps
def rel_seconds(pid):
    t = mv.point_time(pid)
    return -1.0 if t is None else colmap4d.rebase_to_seconds_f32(t, t0)  # timeless -> sentinel

# e.g. build a per-point time attribute alongside xyz/rgb for a 4DGS init seed:
seed = [(*mv.base.points3D[pid].xyz, rel_seconds(pid)) for pid in sorted(mv.point_ids())]
```

Keep `currentTime` / `σ_t` render uniforms in the same rebased (relative-second) frame.

---

## If you are a PRODUCER (writing colmap4d data)

**Iron law: write sidecars through the package API — never hand-format the files.** The byte/line
details (int64 ns, count-prefixed `.bin`, last-wins) are the package's job; hand-rolling them is how
silent corruption enters.

1. **Is there already a converter?** Check `src/colmap4d/convert/` and spec Part IV before writing
   anything. Per-frame COLMAP → single colmap4d is implemented (`convert_per_frame_colmap`).
2. **Private pipeline pattern:** do your bespoke logic to derive `(image_id → t_ns)` and
   `(point3d_id → t_ns)` as plain ints, then hand off to the API to serialize:

   ```python
   from colmap4d import write_times_txt, write_points_t_txt, write_time_meta
   write_times_txt("sparse/times.txt", {img_id: t_ns, ...})       # int ns
   write_points_t_txt("sparse/points_t.txt", {pt_id: t_ns, ...})  # PARTIAL — omit timeless points
   write_time_meta("sparse/time_meta.json", {
       "colmap4d_spec": "1.0", "time_convention": "mid_exposure", "clock_domain": "utc_ntp",
   })                                                              # SHOULD accompany times
   ```
   (`.bin` variants: `write_times_bin` / `write_points_t_bin`.) Point `t` defaults to the track
   observation centroid (spec II.1); declare it via `time_meta.points_t_method` if one method fits.
3. **Always validate after writing.** `problems = colmap4d.validate_full("sparse")`;
   `colmap4d.validate.exit_code(problems)` is non-zero on any **ERROR** — **fix every ERROR**
   (duplicate ids, camera/device conflicts) and **surface every WARNING to the user** (dangling ids,
   missing `time_meta`).

---

## If you are an INDEPENDENT IMPLEMENTER (a second reader/writer in C++/Rust/JS/…)

The producer "iron law" above does **not** apply to you — you are *building* the parser, so
hand-writing serialization/deserialization is exactly the right path. Your contract instead:

- **`spec/colmap4d-v1.0.md` Part I is the only normative source.** Implement it directly; ignore
  the Python package except as a reference.
- **Conformance = the goldens.** Check out the repo at the spec tag (`v1.0`), and make your
  implementation read every `conformance/golden/**` fixture to the values its `README.md` documents
  (partial map + timeless, int64 ns, last-wins duplicates, ignored dangling ids, the `.bin` byte
  layout). Passing them all = conformant.
- Evolve with the spec: new versions only *add optional* fields (readers must tolerate unknown
  fields), tracked in `spec/CHANGELOG.md`.
