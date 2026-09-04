# Converters — producing colmap4d data

Converters are the format's **writer** side: they turn data that already exists into colmap4d so
its ecosystem grows. Before writing anything, check whether a converter already covers your source.

**Iron law:** write sidecars through the package API (`colmap4d.write_times_*`,
`write_points_t_*`, `write_time_meta`) — never hand-format the files. The byte/line details
(int64 ns, count-prefixed `.bin`, last-wins) are the package's job. After producing, always
`validate_full` and fix errors (see [After you write](#after-you-write)).

## Available

### per-frame COLMAP → single colmap4d — **Python API (implemented)**

Merge N per-frame COLMAP reconstructions (`colmap_0/…`, `colmap_1/…`, each the same rig at one
instant) into one colmap4d model, each image and point carrying its frame's timestamp.

```python
from colmap4d.convert.per_frame_colmap import convert_per_frame_colmap

res = convert_per_frame_colmap(
    ["colmap_0/sparse/0", "colmap_1/sparse/0", ...],   # ordered per-frame model dirs
    "out/sparse",                                       # output colmap4d model
    # frame_times_ns=[...],        # explicit per-frame ns; omit -> synthetic index * interval
    # frame_interval_ns=33_333_333 # 30 fps default when timestamps are synthetic
)
# res.num_frames / num_cameras / num_images / num_points / synthetic_times
```

Notes:
- **Points are kept per-frame independent — no cross-frame dedup** (spec OPEN-6; a persistent
  corner seen in N frames is N honest samples). `dedup_points=True` is reserved and refused.
- Synthetic timestamps (no `frame_times_ns`) are marked in `time_meta` (`clock_domain`
  `synthetic_uniform`); pass real per-frame times when you have them.
- Reads base models via pycolmap (`pip install 'colmap4d[model]'`); writes text model + sidecars.
- **Images:** the converter writes only the sparse model + sidecars. To lay out the image files so
  a viewer can resolve each camera's photo, run
  [`scripts/assemble_images.py`](../scripts/assemble_images.py) (places images by model NAME;
  optional recompression to fit a viewer's archive-size cap).

A `colmap4d convert` **command-line interface is not built yet** — use the Python API above.

## Planned

Not yet implemented (contributions welcome, via the Tier-1 flow in
[`CONTRIBUTING.md`](../CONTRIBUTING.md)):

- **single COLMAP + named image sequence** → colmap4d
- **nerfstudio** `transforms.json` (bidirectional, `time` field)
- **Neu3D / DyNeRF**, **HyperNeRF / Nerfies**, **EuRoC / TUM**

## Writing your own producer

For a bespoke pipeline: do your private logic to derive `image_id → t_ns` and `point3d_id → t_ns`
as plain ints, then hand off to the API to serialize.

```python
from colmap4d import write_times_txt, write_points_t_txt, write_time_meta

write_times_txt("out/sparse/times.txt", {image_id: t_ns, ...})       # int nanoseconds
write_points_t_txt("out/sparse/points_t.txt", {point3d_id: t_ns, ...})  # PARTIAL: omit timeless points
write_time_meta("out/sparse/time_meta.json", {
    "colmap4d_spec": "1.0", "time_convention": "mid_exposure", "clock_domain": "utc_ntp",
})
```

- Point `t` defaults to the track observation centroid (spec II.1); if one method describes the
  whole file, declare it via `time_meta.points_t_method`.
- `.bin` variants: `write_times_bin` / `write_points_t_bin`.

## After you write

```python
import colmap4d
from colmap4d.validate import exit_code

problems = colmap4d.validate_full("out/sparse")
# exit_code(problems) is non-zero on any ERROR (duplicate ids, device/camera conflicts) — fix them.
# WARNINGs (dangling ids, missing time_meta) do not fail; surface them to the user.
```
