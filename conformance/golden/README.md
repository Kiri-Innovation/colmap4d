# Conformance golden models

These models are **hand-authored**, not dumped by any implementation. They are the
executable definition of the spec: a second implementation (C++/Rust/…) is conformant
iff it reads them to the values documented here. Do **not** regenerate them with a tool —
that would make the reference implementation, rather than the spec, the source of truth.

Only the **normative** facts below are guaranteed. Incidental choices (line order,
whitespace, the specific centroid values in `points_t.txt`) are NOT normative and a
conformant reader must not depend on them.

## `minimal_scene/` — a colmap4d model

Standard COLMAP text model (`cameras.txt`, `images.txt`, `points3D.txt`) plus the three
colmap4d sidecars (`times.txt`, `points_t.txt`, `time_meta.json`), all under `sparse/`.

- 2 cameras (PINHOLE), 3 images, 6 points; 2D↔3D correspondences are internally consistent
  (every `points3D` track entry `(IMAGE_ID, POINT2D_IDX)` matches that image's POINTS2D line).

### `times.txt` — expected (image_id → t_ns)
```
1 -> 1699999999123456789
2 -> 1699999999156789012
3 -> 1699999999140000000
```

### `points_t.txt` — expected (point3d_id → t_ns), PARTIAL map
```
1 -> 1699999999140081934
2 -> 1699999999140122901
3 -> 1699999999131728395
4 -> 1699999999148394506
6 -> 1699999999140081934
```
**Point 5 is intentionally absent** → temporally-unbounded → a reader MUST report "no
timestamp / present at all t" for point 5 (never 0, never an error).

### `time_meta.json` — expected
- `time_convention == "mid_exposure"`, `clock_domain == "utc_ntp"`, `time_unit == "ns"`.
- `devices` has `device_a` (camera_ids [1], sync_err_ns 3000000) and `device_b`
  (camera_ids [2], sync_err_ns 3500000).

This is the **with-`camera_ids`** case. Device attribution is optional provenance, not a
conformance gate (spec Part I, OPEN-1); the time layer is unaffected by it.

## `no_devices/` — a colmap4d model with NO device attribution

A conformant colmap4d model (1 camera, 2 images, 2 points, all three sidecars) whose
`time_meta.json` has **no `devices` key at all** — representative of data converted from
sources with no device concept (Neu3D, HyperNeRF, …). Loading MUST succeed and yield a
normal time layer:
```
times    : 1 -> 1699999999100000000, 2 -> 1699999999133333333
points_t : 1 -> 1699999999116666666, 2 -> 1699999999116666666
```
Together with `minimal_scene/`, this proves both **with** and **without** `camera_ids` are
conformant.

## `plain_colmap/` — backward-compatibility baseline

A standard COLMAP model with **no sidecars at all** (1 camera, 2 images, 2 points).
Loading it as colmap4d MUST succeed and yield: empty `times`, empty `points_t`
(⇒ all points temporally-unbounded), `time_meta == None`. No file-not-found error.

## `dup_ids/` — duplicate id, canonical last-wins (spec I.D)

`times.txt` lists `IMAGE_ID 2` twice. A conformant reader MUST resolve this **last-wins**:
```
times : 1 -> 1699999999100000000, 2 -> 1699999999155555555   (NOT ...120000000)
```
`validate` reports the duplicate as an **ERROR** (non-zero exit). A strict reader MAY
instead raise. This pins every implementation to the same deterministic tolerance.

## `dangling_ids/` — dangling id, consumer ignores, validate warns (spec I.D)

`points_t.txt` references `POINT3D_ID 999`, absent from the model (points are 1 and 2).
- The pure sidecar reader (no model) returns all three ids verbatim: `{1, 2, 999}`.
- A model-aware consumer MUST ignore model-absent ids ⇒ effective `{1, 2}`.
- `validate` reports 999 as a **WARNING** (exit 0; `--strict` promotes to failure), and the
  message names the SfM whole-model misalignment risk, not merely "unknown id".
