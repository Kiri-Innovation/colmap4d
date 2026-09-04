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
  fields are whitespace-separated.
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
- `points_t` MAY reference only a subset of the model's points; it MAY also carry ids absent
  from the model. Duplicate and dangling ids are governed by I.D below.

`times` (per-image) is, by contrast, expected to cover images that carry a timestamp; an
image absent from `times` simply has no known time (same null semantics). *(Informative: for a
given capture, per-image coverage is normally complete — an observation, not a requirement.)*

> **Informative rationale (author's stance).** In the *ideal* dense spacetime model, static
> structure is not a special case: a persistent wall corner is honestly sampled as one point
> *per frame* (e.g. 900 samples at 900 times), so it is always within any time window and
> needs no special rule. The temporally-unbounded semantic exists only as a convenience for
> **sparse** representations (a single reconstruction where that corner is one point at its
> track-centroid time). It is a rendering/expression convenience and **does not assert, and
> must not be read as, a static/dynamic classification** — deciding what is background is the
> job of downstream reconstruction, not of this format. See white paper §3.3, Q4, Q6.

## I.B — Timestamps are int64 nanoseconds; `t0` is derived, not stored — **FROZEN**

Part I governs only the **storage layer**. How a consumer rebases for rendering is a best
practice (II.5), not a protocol obligation — Part I imposes no MUST on any consumer
rendering/compute process.

- All timestamps (`T_NS` in `times`/`points_t`) are **signed 64-bit integer nanoseconds**.
  `float64` seconds would lose sub-millisecond precision on epoch-scale values and is
  forbidden as a storage type. int64 signed is chosen so non-epoch or pre-epoch clock
  domains remain representable; UTC-epoch ns stays valid through year 2262.
- The semantic meaning of the integer (epoch, monotonic boot clock, …) is declared by
  `time_meta.clock_domain` (I.C); the format stores the **resolved global time**, not raw
  per-device clocks.
- **`t0` is not stored; it is derived — over the effective record set.** `t0` is the minimum
  timestamp over the model's **effective** records: after joining the sidecars to the base model
  and dropping dangling ids (I.D), take `min` over `times` ∪ `points_t`. Defining t0 on the
  joined set (not the raw sidecar records) makes it **independent of dangling early timestamps**,
  so two conformant implementations compute the *same* t0 — a consistency requirement (a dangling
  id with an early time would otherwise shift t0 in one reader but not another). The reference
  implementation: `ModelView.t0_ns()` computes this; `Sidecars.t0_ns()` (no base model available)
  returns `min` over the raw records and is therefore an **approximation** that MAY be earlier if
  a dangling id carries an early timestamp. Storing t0 is forbidden (it would be a second,
  drift-prone copy).

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

**`devices` is provenance, not a conformance gate.** A capture "device" is a concept
colmap4d introduces in `time_meta`; the host format has no corresponding entity (not even
COLMAP's `rigs`/`frames`). The core promise is only "one `t` per image, one `t` per point" —
device attribution is *where a timestamp came from*, not the timestamp itself. Therefore the
MUST layer fully tolerates `devices` being absent, present-but-empty, or arbitrarily shaped,
and **conformance does not check it**. Models converted from data with no device concept
(Neu3D, HyperNeRF, …) are fully conformant with no `devices` at all.

Each **device object** (all fields OPTIONAL, provenance for one capture device):

| field | req. | type | meaning |
|-------|------|------|---------|
| `camera_ids` | MAY | array<int> | COLMAP `CAMERA_ID`s produced by this device. Direction is device → `[CAMERA_ID, …]`, one-to-**many** (one phone that zooms or is split across segmented SfM runs yields several cameras). This is a provenance hint, not a required binding; when absent, tools MAY infer device grouping heuristically (see Part II / viewer docs) but correctness is not guaranteed. |
| `raw_clock` | MAY | string | device's native clock, e.g. `"android_boottime"`. |
| `sync_method` | MAY | string | how the device clock was aligned, e.g. `"lan_ntp"`. |
| `sync_err_ns` | MAY | int | estimated clock-alignment uncertainty in ns. Surfaced to downstream as an observation-noise input; also the natural lower bound for a viewer's default ε (Part II). |
| `offset_samples` | MAY | array<[int, int]> | raw `[t_mono_ns, offset_ns]` clock-offset observations ("store observations, not conclusions"; enables re-solving drift later). |

A reader MUST tolerate unknown top-level and unknown device fields (forward compatibility):
ignore, do not error.

### `time_meta` absent — undeclared relative time

When a `times` sidecar exists, `time_meta.json` SHOULD accompany it. When it does **not**, the
timestamps are still valid but carry **no declared semantics**: a consumer MUST treat them as
*relative time of undeclared clock domain and exposure convention*. Concretely, a consumer:

- MAY order images/points by `t` and take differences within the one model (the numbers are a
  monotonic time axis);
- MUST NOT compare these timestamps against another model's, or against wall-clock/epoch time
  (the clock domain is unknown — the values may be epoch ns, a device boot clock, or synthetic);
- MUST NOT assert a `clock_domain` or `time_convention` (e.g. mid-exposure) that was not declared.

`validate` reports a **WARNING** when `times` is present but `time_meta` is absent (a producer
SHOULD declare provenance); it is not an error, because timestamp-only models are legitimate
(e.g. quick conversions) and remain fully usable as a relative axis.

**Consistency rule (checked by `validate`, not by conformance).** If `camera_ids` are
present, a given `CAMERA_ID` MUST appear under at most one device — one image cannot be
timestamped by two clocks. `MAY` means the field is optional to write, not free to write
inconsistently.

## I.D — Duplicate and dangling ids (reader tolerance is normative) — **FROZEN**

Reader tolerance is specified, not left to implementations: if one reader took first-wins
and another last-wins, the same file would yield two different timestamps — a silent data
divergence, worse than a crash.

- **Duplicate id.** A writer MUST NOT emit two records with the same id in one sidecar. A
  reader SHOULD accept such a file and resolve duplicates **last-wins** (the last occurrence
  in file order is authoritative). A reader MAY offer a strict mode that rejects duplicates
  instead. `validate` reports a duplicate as an **ERROR** (a writer-side bug; the shadowed
  timestamps are lost).
- **Dangling id.** A sidecar MAY reference an id absent from the base model — this is *not*
  forbidden (legitimate use: keeping a sidecar as a superset of a filtered/subset model). A
  consuming reader (one that has the base model) MUST ignore ids not present in the model.
  `validate` reports dangling ids as a **WARNING** by default, promotable to failure under
  `--strict`. The warning MUST name the real hazard: after an SfM re-run, the danger is not
  the id that vanished but a surviving id now pointing at a *different* entity (a silently
  mislabeled timestamp) — a dangling id is often its only visible symptom, so the fix is to
  regenerate the sidecar, not to trust the survivors.

## I.E — Binary sidecar layout — **FROZEN**

`times.bin` and `points_t.bin` are the binary forms of `times` / `points_t` (a reader MUST
prefer `.bin` when both forms are present). All integers are **little-endian**. Each file is a
**uint64 record count** followed by exactly that many fixed-width records:

```
times.bin     : uint64 count, then count × ( uint32 IMAGE_ID,   int64 T_NS )
points_t.bin  : uint64 count, then count × ( uint64 POINT3D_ID, int64 T_NS )
```

- `T_NS` is the same signed int64 nanoseconds as the text form (I.B).
- Id widths match COLMAP's own integer types so the sidecars align with the base model:
  image ids are **uint32** (COLMAP `image_t`) and point ids are **uint64** (COLMAP `point3D_t`).
- There is no per-record delimiter, no header beyond the count, and no trailing padding: one
  `times` record is exactly 12 bytes, one `points_t` record exactly 16 bytes. File size MUST be
  `8 + count × record_size`.
- Duplicate and dangling ids follow I.D identically to the text form (a reader collapses
  duplicates **last-wins**). `time_meta` has no binary form — it is always JSON.

This section is self-contained: a conformant `.bin` reader can be written from this text plus
the `conformance/golden/minimal_scene_bin/` fixture, with no reference to any implementation.

### Explicitly NOT in Part I (by decision)

- **Filename-as-timestamp conventions** are NOT a protocol rule. COLMAP imposes no filename
  constraints and neither does colmap4d. Any such convention is Part II best practice only,
  and if a `times` sidecar exists it is authoritative over any filename heuristic.
- **`groups.txt` / pseudo-frames** are NOT in the v1 rule layer. Grouping is a derived view
  (Part III).
- **Per-point life-spans, σ_t, fade curves, dynamic/static masks** are NOT stored. They are
  render parameters or v2 optional layers (white paper §3.3, Q4).

---

# Part II — Best practices (Informative)

Everything in Part II is **informative and RECOMMENDED**, never binding. A model that ignores
these conventions is still fully conformant; **conformance never checks Part II.** These are
the choices a producer *should* make when the spec leaves room, so that independent tools
converge on the same sensible defaults.

## II.1 — Point timestamp: track-observation centroid (default)

`points_t[p]` SHOULD be the **centroid (arithmetic mean) of the observation times** of point
`p`'s track — i.e. the mean of `times[image_id]` over every image that observes `p`. This is
the natural "when was this point seen" summary and matches the white paper's recommendation
(§3.3). It degenerates correctly: a point whose observations all share one instant (e.g. one
frame of a synchronized array) gets exactly that instant.

Alternatives, for producers with a reason:
- **Median** observation time — more robust when a track has a few outlier observations (e.g.
  a mistaken match across distant frames); RECOMMENDED over the mean for long, noisy tracks.
- **First observation** time — appropriate when `t` is meant as an "onset"/birth time rather
  than a central tendency (e.g. event-like features).

Whatever the choice, it is a summary of observations and MUST NOT be read as a life-span or a
static/dynamic label (that is a render parameter / downstream concern, see Part I.A).

## II.2 — Default ε for pseudo-frame grouping

When a consumer must group images into pseudo-frames (Part III), it needs an ε (time window).
`time_meta.devices[*].sync_err_ns` is the **physical lower bound**: you cannot resolve frames
finer than the clock-alignment uncertainty. So the RECOMMENDED default is

```
ε_default = k · max(sync_err_ns over all devices),   k ≈ 2–5   (k = 3 suggested)
```

Rationale: grouping tighter than the sync error splits images that are physically simultaneous
into different pseudo-frames (false negatives); `k` gives margin above the noise floor without
merging genuinely distinct instants. If no `sync_err_ns` is declared, a consumer SHOULD fall
back to a fraction of the median inter-image time gap and surface ε as a user control (the
white paper's ε-slider). ε is always the *consumer's* choice per its motion scale (white paper
Q2); this is only a starting default.

## II.3 — Filename-as-timestamp fallback (no `times` sidecar)

COLMAP imposes no constraints on image names, and neither does colmap4d — so this is a
**best-practice fallback, never a protocol rule** (Part I only recognizes the `times` sidecar;
if `times` exists it is authoritative and any filename heuristic MUST be ignored).

When no `times` sidecar is present, a tool MAY infer an ordering/time from image names that
encode it (e.g. `frame_000123.png`, `cam03/000042.jpg`, zero-padded sequence numbers). Such an
inference SHOULD be surfaced as "inferred from filename", never presented as ground-truth time,
and SHOULD be treated as ordinal (sequence) unless the names encode an actual physical time.

This is a low-adoption-cost bridge: the `ColmapUtil` viewer already derives a per-image frame
index from filenames (its `byRigFrame` colouring builds an image→frame-index map from names).
A colmap4d-aware viewer can reuse exactly that path as the sidecar-less fallback, then upgrade
to the real `times` sidecar when present.

## II.4 — Heuristic device grouping when `camera_ids` is absent

`time_meta.devices[*].camera_ids` is optional (Part I, OPEN-1). When it is absent but a viewer
wants per-device rows (e.g. the exposure Gantt chart), it MAY group images by a filename prefix
or embedded camera token (`cam03/...`, `camXX.png`). This grouping MUST be labeled "inferred
from filename" in the UI and is never authoritative — it is a viewer convenience documented
with the viewer, not part of the model.

## II.5 — Rebasing timestamps for GPU rendering

A consumer that uploads time to the GPU SHOULD NOT place raw int64 ns into a float32 vertex
attribute: ~1.7e18 ns overflows float32's 24-bit mantissa. It SHOULD first **rebase**: compute
`t0` (the model's effective min, I.B) and upload `(t − t0)` in **seconds as float32**. Over a
single capture this spans seconds and retains ≈microsecond precision. `currentTime` / `σ_t`
uniforms SHOULD use the same rebased frame. (This is a rendering best practice, not a protocol
obligation: it constrains a consumer's compute process, which Part I does not govern and
conformance cannot check. The reference implementation provides `rebase_to_seconds_f32(t, t0)`.)

---

# Part III — Derived views (Informative, Optional)

A derived view is anything a tool can recompute from Part I data. It is never required and
never authoritative; if a materialized derived file disagrees with the core sidecars, the
**core sidecars win**. Materializing one is only a cache/convenience for consumers that want a
particular slicing precomputed.

## III.1 — `groups.txt` (pseudo-frame grouping)

Some classic downstreams (per-instant multi-view triangulation, multi-view consistency losses)
want "the images at a moment `t`". `groups.txt` OPTIONALLY materializes one such grouping. It
is a pure function of `times` + a chosen ε (Part II.2), recomputable by
`group_by_time(times, eps_ns)`, so it MUST record the parameters that produced it.

On-disk format (text; same comment/whitespace rules as Part I sidecars):

```
# groups.txt — derived, eps_ns=8000000, method=greedy_window
# GROUP_ID, T_CENTER_NS, IMAGE_IDS...
1 1699999999130000000 3 17 42 88
2 1699999999200000000 5 19 44 90
```

- The **header comment MUST record generation parameters**: at least `eps_ns` and `method`
  (e.g. `greedy_window`, `kmeans`). Without them the grouping is not reproducible and MUST be
  treated as opaque/untrusted.
- Each data line: `GROUP_ID` (int ≥ 1), `T_CENTER_NS` (int64 ns, the group's representative
  time — e.g. mean/median of members), then one or more `IMAGE_ID`s.
- Grouping is a partition SHOULD-property: each image SHOULD appear in at most one group; an
  image in no group is simply ungrouped for that ε.
- A `groups.bin` MAY mirror this later; not defined in v1 (text only).
- **Authority:** if `groups.txt` disagrees with recomputing from `times`+ε (e.g. `times` was
  edited afterward), `times` wins and `groups.txt` MUST be regenerated. A consumer that cannot
  verify the parameters SHOULD recompute rather than trust the file.

The reference implementation exposes `colmap4d.groups.group_by_time(times, eps_ns)` (and will
read/write `groups.txt`); it is a tool output, not part of the model's normative content.

---

# Part IV — Ecosystem (Informative)

colmap4d is adopted through tools, not through the spec — the spec earns trust, the tools earn
adoption. The reference repo (`colmap4d/colmap4d`) is organized around three roles:

- **Reference implementation (the reader/consumer entry).** `colmap4d.sidecar` is the
  zero-dependency time layer; `colmap4d.colmap_io` is a zero-dependency COLMAP base-model
  reader (classic txt/bin); `colmap4d.model` joins them into a `ModelView` and prefers
  `pycolmap` when installed (optional extra `colmap4d[model]`) for authoritative parsing and
  3.12 rigs/frames. `import colmap4d; colmap4d.load_model_view(dir)` reads a timestamped model
  with no compiled dependency.
- **Converters (the "writer" side — where the format's data comes from).** `colmap4d.convert`:
  `per_frame_colmap` (N per-frame COLMAP dirs → one colmap4d model) is implemented; nerfstudio
  `transforms.json`, Neu3D/DyNeRF, HyperNeRF/Nerfies are planned. Plus `colmap4d validate`
  (graded ERROR/WARNING with exit codes).
- **Viewer (the "why you'd want this" — a separate repo).** 3D free view + time scrubber,
  ε-window camera gating, GPU time-kernel point filtering, exposure Gantt chart. Kept separate
  for stack/build/cadence reasons; today the `ColmapUtil` React viewer (adjacent repo) is the
  starting point and will be linked once published under the org.

Datasets (a real non-synchronized capture with raw timestamps + offset samples, and a 4DGS
loader patch) ship as `examples/` + release assets until there are enough to warrant their own
`sample-data` repo.

**Upstream strategy.** Propose per-image/frame timestamp fields to COLMAP (a repo Discussion,
with this design + reference implementation). If accepted, colmap4d becomes the transition-period
reference; if not, it holds the community-consensus direction. The public draft spec + repo
Discussions are themselves the evidence chain for that proposal.

---

# Open questions surfaced while authoring the golden

These were found by hand-writing `conformance/golden/minimal_scene/` and forcing every
ambiguity to a decision. Provisional choices are marked; revisit before leaving draft. See
also `docs/open-questions.md`.

- **OPEN-1 (device↔model binding).** *Settled: device attribution stays out of the core
  protocol.* "Device" is a `time_meta` provenance concept with no host-format entity, so it
  cannot be a conformance gate. `camera_ids` is retained as an optional device→`[CAMERA_ID]`
  (one-to-many) hint (MAY, not conformance-checked); `validate` enforces that a `CAMERA_ID`
  appears under at most one device. When absent, a viewer MAY infer grouping by filename
  prefix, labeled "inferred". See the `time_meta` section above.
- **OPEN-2 (`t0` domain).** `t0` is defined as the min over `times` ∪ `points_t`. A track
  centroid normally lies within its images' time span, so this usually equals min(`times`),
  but the union is used to stay well-defined when only one sidecar is present.
- **OPEN-3 (duplicate / dangling ids).** *Settled (see I.D): tolerance is normative, not
  implementation-defined.* Writers MUST NOT duplicate ids; readers accept last-wins (optional
  strict mode). Dangling ids are permitted; consumers MUST ignore model-absent ids. `validate`
  grades duplicate = ERROR (non-zero exit), dangling = WARNING (exit 0, `--strict` promotes),
  with the dangling message naming the SfM whole-model misalignment risk.
- **OPEN-4 (id stability).** COLMAP `IMAGE_ID`/`POINT3D_ID` are not stable across re-runs of
  SfM (white paper §6). Mitigation of record: sidecars share the model directory's lifecycle
  and are (re)produced by the importer together with the model. No format change; noted so
  consumers never cache sidecars against a stale model.
- **OPEN-5 (empty vs absent).** An empty `points_t` (present, zero records) and an absent
  `points_t` are defined as equivalent (all points temporally-unbounded). Confirmed, not a
  problem — recorded so no future reader distinguishes them.
