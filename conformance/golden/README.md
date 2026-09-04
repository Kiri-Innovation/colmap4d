# Conformance golden models

These models are **hand-authored**, not dumped by any implementation. They are the
executable definition of the spec: a second implementation (C++/Rust/…) is conformant
iff it reads them to the values documented here. Do **not** regenerate them with a tool —
that would make the reference implementation, rather than the spec, the source of truth.

Only the **normative** facts below are guaranteed. Incidental choices (line order,
whitespace, the specific centroid values in `points_t.txt`) are NOT normative and a
conformant reader must not depend on them.

## Status: normative fixtures

These goldens are the **executable definition of the format** — the normative facts documented
per fixture below are frozen alongside the spec. Conformance is defined against Part I of the
spec **plus** these fixtures; where a subtle question is under-specified in prose, the golden
bytes/values decide.

**Change constraint.** Editing a golden's normative content (the byte layout, the documented
ids/timestamps, the pass/fail behavior it pins) **is a protocol change** and MUST go through the
same spec-PR process as editing Part I — with a `spec/CHANGELOG.md` entry and category. Purely
incidental edits (a comment, a non-normative field, adding a *new* fixture) are ordinary changes.
Never regenerate a golden from an implementation to make a test pass; fix the implementation.

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

## `minimal_scene_bin/` — binary sidecar layout (spec I.E)

Same base model + values as `minimal_scene/`, but the sidecars are `times.bin` / `points_t.bin`
authored to spec I.E (little-endian, uint64 count prefix; hand-authored bytes, not dumped by the
implementation). Byte layout:

```
times.bin    : <Q count=3>  then 3 × <I image_id><q t_ns>   → file size 8 + 3*12 = 44 bytes
points_t.bin : <Q count=5>  then 5 × <Q point3d_id><q t_ns> → file size 8 + 5*16 = 88 bytes
```

A conformant `.bin` reader MUST decode them field-for-field to the SAME values documented for
`minimal_scene/` above (point 5 absent from `points_t` ⇒ temporally-unbounded). `dup_ids/times.bin`
carries the same duplicate as `dup_ids/times.txt` (image 2 twice) and MUST collapse **last-wins**
(→ `1699999999155555555`), exactly like the text form.

## `times_only/` — times without time_meta (spec I.C)

A model with `times.txt` but **no** `time_meta.json` (and no `points_t`). Loading MUST succeed;
the timestamps are a valid relative axis but carry no declared clock domain / exposure
convention (MUST NOT be compared across models or to wall-clock). `validate` reports a
**WARNING** `time_meta.absent` (exit 0; `--strict` promotes).

## `dangling_early/` — t0 is the effective (dangling-dropped) min (spec I.B)

`points_t` has a dangling id `999` at an EARLY time (`…000000000`); in-model points/images are at
`…200000000`. `ModelView.t0_ns()` (model-joined) MUST be `…200000000` — the dangling early time
must not shift t0. `Sidecars.t0_ns()` (no model) is the approximation and returns `…000000000`.
