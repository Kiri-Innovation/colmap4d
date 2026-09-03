# colmap4d format specification — v0.2-draft

> Status: **DRAFT**, not frozen. Only the three Part I rules marked **FROZEN** below are
> settled enough to build code against (this is the WP0 freeze gate); everything else is a
> placeholder or subject to change. Do not tag v1.0 or treat conformance goldens as
> normative until this document leaves draft.

## What colmap4d is

> **colmap4d = a standard COLMAP sparse model + one timestamp per image + one timestamp
> per point.** Every image and every sparse point is an independent sample in spacetime
> `(x, y, z, t)`. Frame synchronization is just the special case of timestamps being close;
> any discretization (pseudo-frames, life-spans, fade in/out) is a derived view or a render
> parameter, never stored data.

A colmap4d model is a standard COLMAP sparse model directory (`cameras`, `images`,
`points3D`, and optionally `rigs`/`frames`, in `.txt` or `.bin`) with **new sidecar files
added and no existing file changed**. A tool that ignores the sidecars sees a completely
ordinary COLMAP model — this backward compatibility is a hard guarantee, not a nicety.

This document is grounded in the colmap4d white paper (design rationale, motivation, and
Q&A) but is the *normative* surface; where the two differ, decisions recorded here win.

## Document structure & conformance language

The specification is layered so that RFC-2119 obligations apply to as little as possible:

- **Part I — Protocol rules (Normative).** MUST / SHOULD / MAY per
  [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119). This is the only part with binding
  force. A conformant reader/writer is defined solely against Part I + the conformance
  goldens in `conformance/golden/`.
- **Part II — Best practices (Informative).** RECOMMENDED conventions. Ignoring them
  produces a still-valid model.
- **Part III — Derived views (Informative, Optional).** Things any tool can recompute from
  Part I data (e.g. pseudo-frame grouping). Never required, never authoritative.
- **Part IV — Ecosystem (Informative).** Reference implementation, converters, viewer,
  upstream strategy.

The key words MUST, MUST NOT, REQUIRED, SHOULD, SHOULD NOT, RECOMMENDED, MAY, and OPTIONAL
in Part I are to be interpreted as described in RFC 2119.

---

# Part I — Protocol rules (Normative)

A colmap4d model adds up to three sidecar files, placed **in the same directory as the
COLMAP model files** they annotate (the directory containing `cameras.*`, `images.*`,
`points3D.*`; e.g. `sparse/` or `sparse/0/`):

| file | contents | keyed by |
|------|----------|----------|
| `times.txt` / `times.bin` | per-image timestamp | COLMAP `IMAGE_ID` |
| `points_t.txt` / `points_t.bin` | per-point timestamp (partial) | COLMAP `POINT3D_ID` |
| `time_meta.json` | time-axis semantics & provenance | — |

General rules:

- A writer MUST NOT modify any standard COLMAP file when producing a colmap4d model.
- Each sidecar is independently OPTIONAL. Any subset (including none) MAY be present. A
  reader MUST treat an absent sidecar as empty and MUST NOT error on its absence. A model
  with no sidecars is exactly a standard COLMAP model (the backward-compatibility baseline).
- For a given sidecar, a writer SHOULD emit either the `.txt` or the `.bin` form. If both
  forms of the same sidecar are present, a reader MUST prefer `.bin` (matching COLMAP).
- `time_meta` is JSON only; there is no binary form.
- Text sidecars: lines beginning with `#` and blank lines are comments and MUST be ignored;
  fields are whitespace-separated; a sidecar MUST NOT contain two records with the same id.
- Record ordering within a sidecar is NOT normative; a reader MUST NOT depend on it.

## I.A — `points_t` is a partial map; missing points are temporally-unbounded — **FROZEN**

`points_t` maps `POINT3D_ID → T_NS`. It is a **partial map**: a sparse point MAY have no
entry.

- A point that has **no** `points_t` entry is **temporally-unbounded**: the model asserts
  no temporal localization for it; equivalently, it is present at all `t`. A reader MUST
  represent this as "no timestamp" (e.g. a null/None sentinel) and MUST NOT substitute `0`
  or raise.
- An absent `points_t` sidecar is equivalent to an empty one: **every** point is
  temporally-unbounded. This is what makes a plain COLMAP model render as fully static in a
  colmap4d viewer (the backward-compatibility baseline).
- `points_t` MAY reference only a subset of the model's points, and every key SHOULD
  correspond to an existing `POINT3D_ID` (dangling keys are a `validate` warning, not a
  parse error).

`times` (per-image) is, by contrast, expected to cover images that carry a timestamp; an
image absent from `times` simply has no known time (same null semantics), but per-image
coverage is normally complete for a given capture.

> **Informative rationale (author's stance).** In the *ideal* dense spacetime model, static
> structure is not a special case: a persistent wall corner is honestly sampled as one point
> *per frame* (e.g. 900 samples at 900 times), so it is always within any time window and
> needs no special rule. The temporally-unbounded semantic exists only as a convenience for
> **sparse** representations (a single reconstruction where that corner is one point at its
> track-centroid time). It is a rendering/expression convenience and **does not assert, and
> must not be read as, a static/dynamic classification** — deciding what is background is the
> job of downstream reconstruction, not of this format. See white paper §3.3, Q4, Q6.

## I.B — Timestamps are int64 nanoseconds; rendering rebases to float32 relative seconds — **FROZEN**

- All timestamps (`T_NS` in `times`/`points_t`) are **signed 64-bit integer nanoseconds**.
  `float64` seconds would lose sub-millisecond precision on epoch-scale values and is
  forbidden as a storage type. int64 signed is chosen so non-epoch or pre-epoch clock
  domains remain representable; UTC-epoch ns stays valid through year 2262.
- The semantic meaning of the integer (epoch, monotonic boot clock, …) is declared by
  `time_meta.clock_domain` (I.C); the format stores the **resolved global time**, not raw
  per-device clocks.
- **Rendering / GPU contract.** Consumers that upload time to the GPU MUST NOT place raw
  int64 ns into a float32 vertex attribute: ~1.7e18 ns overflows float32's 24-bit mantissa.
  A consumer MUST first **rebase**: compute `t0 = min` timestamp over the model and upload
  `(t − t0)` in **seconds as float32**. Over a single capture this spans seconds and retains
  ≈microsecond precision. `currentTime` / `σ_t` uniforms MUST use the same rebased frame.
- **`t0` is not stored.** It is derived as the minimum timestamp across the model
  (over `times` ∪ `points_t`), giving a single source of truth. Storing it would create a
  second, drift-prone copy. The reference implementation provides `rebase_to_seconds_f32(t, t0)`
  and `Sidecars.t0_ns()`.

## I.C — `time_meta.json` fields — **FROZEN (top-level field set)**

`time_meta.json` is a single JSON object recording the semantics and provenance of the time
axis. Top-level fields:

| field | req. | type | meaning |
|-------|------|------|---------|
| `colmap4d_spec` | SHOULD | string | spec version the file targets, e.g. `"0.2-draft"` |
| `time_convention` | MUST | string | how each image's `t` is defined. MUST be `"mid_exposure"` in v1 (the only defined value); reserved others may follow. |
| `time_unit` | SHOULD | string | MUST be `"ns"` if present (informational; `T_NS` is always ns). |
| `clock_domain` | MUST | string | semantics of the integer, e.g. `"utc_ntp"`, `"utc_gps"`, `"monotonic_boot"`. |
| `devices` | MAY | object | map of `device_id → device object` (below). |

Each **device object** (all fields OPTIONAL, provenance for one capture device):

| field | type | meaning |
|-------|------|---------|
| `camera_ids` | array<int> | COLMAP `CAMERA_ID`s produced by this device — the device↔model binding (see OPEN-1). |
| `raw_clock` | string | device's native clock, e.g. `"android_boottime"`. |
| `sync_method` | string | how the device clock was aligned, e.g. `"lan_ntp"`. |
| `sync_err_ns` | int | estimated clock-alignment uncertainty in ns. Surfaced to downstream as an observation-noise input; also the natural lower bound for a viewer's default ε (Part II). |
| `offset_samples` | array<[int, int]> | raw `[t_mono_ns, offset_ns]` clock-offset observations ("store observations, not conclusions"; enables re-solving drift later). |

A reader MUST tolerate unknown top-level and unknown device fields (forward compatibility):
ignore, do not error.

### Explicitly NOT in Part I (by decision)

- **Filename-as-timestamp conventions** are NOT a protocol rule. COLMAP imposes no filename
  constraints and neither does colmap4d. Any such convention is Part II best practice only,
  and if a `times` sidecar exists it is authoritative over any filename heuristic.
- **`groups.txt` / pseudo-frames** are NOT in the v1 rule layer. Grouping is a derived view
  (Part III).
- **Per-point life-spans, σ_t, fade curves, dynamic/static masks** are NOT stored. They are
  render parameters or v2 optional layers (white paper §3.3, Q4).

---

# Part II — Best practices (Informative) — *TODO, placeholder*

Planned RECOMMENDED conventions (non-binding):
- Point timestamp = centroid of its track's observation times (white paper §3.3).
- Default viewer ε ≳ `max(sync_err_ns)` across devices.
- Filename conventions for the sidecar-less fallback ("filename as timestamp").
- Device↔camera binding recommendation (pending OPEN-1).

---

# Part III — Derived views (Informative, Optional) — *TODO, placeholder*

- `groups.txt`: an OPTIONAL materialized pseudo-frame grouping. Canonical source of truth is
  always `times` + a chosen ε, recomputable via `group_by_time(times, eps_ns)`. If persisted,
  it MUST record its generation parameters (ε, method) and MAY drift from `times`; on drift,
  `times` wins. Format to be specified here.

---

# Part IV — Ecosystem (Informative) — *TODO, placeholder*

- Reference implementation: zero-dependency `colmap4d.sidecar` (time layer) + `colmap4d.model`
  (base model via optional `pycolmap`).
- Converters (the "writer" side): per-frame-COLMAP ↔ single colmap4d, nerfstudio, Neu3D,
  HyperNeRF, …
- Viewer: 3D free view + time scrubber, ε-window camera gating, GPU time-kernel point
  filtering, exposure Gantt chart.
- Upstream strategy: propose per-image/frame timestamp fields to COLMAP; position colmap4d as
  the transition-period reference implementation.

---

# Open questions surfaced while authoring the golden

These were found by hand-writing `conformance/golden/minimal_scene/` and forcing every
ambiguity to a decision. Provisional choices are marked; revisit before leaving draft. See
also `docs/open-questions.md`.

- **OPEN-1 (device↔model binding).** `time_meta.devices` is keyed by a free string
  (`"device_a"`), but images reference `CAMERA_ID`. The golden binds them via a
  `camera_ids: [..]` array on each device object. *Provisional* — alternatives: derive from
  image NAME prefixes, or an explicit `image_id → device` map. Needed for the Gantt chart
  (one row per device).
- **OPEN-2 (`t0` domain).** `t0` is defined as the min over `times` ∪ `points_t`. A track
  centroid normally lies within its images' time span, so this usually equals min(`times`),
  but the union is used to stay well-defined when only one sidecar is present.
- **OPEN-3 (duplicate / dangling ids).** Duplicate ids in a sidecar are forbidden (MUST NOT);
  the reference reader currently last-wins rather than erroring — should `validate` hard-fail?
  Dangling keys (id not in base model) are a `validate` warning, deferred to `validate.py`.
- **OPEN-4 (id stability).** COLMAP `IMAGE_ID`/`POINT3D_ID` are not stable across re-runs of
  SfM (white paper §6). Mitigation of record: sidecars share the model directory's lifecycle
  and are (re)produced by the importer together with the model. No format change; noted so
  consumers never cache sidecars against a stale model.
- **OPEN-5 (empty vs absent).** An empty `points_t` (present, zero records) and an absent
  `points_t` are defined as equivalent (all points temporally-unbounded). Confirmed, not a
  problem — recorded so no future reader distinguishes them.
