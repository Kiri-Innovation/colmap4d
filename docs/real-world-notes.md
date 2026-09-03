# Real-world data notes

First contact between the reference implementation and real captures. Findings that inform
the spec and the reader; not normative.

## 2026-09 — STG per-frame export (spinach, 21 cameras × 50 frames; first 5 frames tested)

Source: per-frame COLMAP models (`colmap_0 .. colmap_4/sparse/0/`) exported by STG, COLMAP
**3.12-style** (each frame carries `rigs.bin` + `frames.bin` alongside the classic three
files), plus `points3D*.ply` products from the 4DGS pipeline.

### Zero-dep `colmap_io` reader vs the 3.12 binaries — it just works
- The pure-Python `colmap_io` reader read all five frames' `cameras.bin` / `images.bin` /
  `points3D.bin` with **no changes**, and was verified **field-for-field equal to pycolmap**
  (camera model/params, image pose quat+translation, name, camera_id, 2D count; point
  xyz/rgb/error, track length). Counts matched exactly, which also proves there is no byte
  misalignment — i.e. **3.12 did not change the classic `images.bin` / `points3D.bin` layout**.
- **Takeaway:** COLMAP 3.12's rig/frame model is additive at the *file* level (new `rigs.bin`,
  `frames.bin`), not a change to the three files colmap4d cares about. The classic layout the
  reader targets remains correct on current exports.

### `rigs.bin` / `frames.bin` — correctly ignored
- `colmap_io.read_model` reads only `cameras`/`images`/`points3D`; it never opens `rigs.bin`
  or `frames.bin`, so their presence has zero effect. This matches the design: colmap4d is a
  sidecar layer over the classic model and does not depend on rigs/frames (white paper Q1).
- Consequence for the converter: rig/frame grouping from the source is **not** propagated;
  poses live in `images` and are preserved verbatim. This is the intended "don't break poses"
  behavior — we neither consume nor emit rigs/frames in v1.

### `points3D*.ply` and other siblings — ignored
- The 4DGS `.ply` products (`points3D.ply`, `points3D_total10.ply`, `points3D_total50.ply`)
  and any non-classic files are never touched. The reader keys off exact file stems.
- **Note for tooling:** a future `validate`/importer that *discovers* models by scanning a
  directory should whitelist the classic stems and explicitly skip `.ply`/`rigs`/`frames`,
  rather than assuming every file is model content.

### Cross-frame `CAMERA_ID` inconsistency (confirmed on real data)
- Frame 0's `cameras.bin` holds **1** shared PINHOLE intrinsic (all 21 images → camera_id 1);
  frames 1–4 hold **21** distinct cameras. So `CAMERA_ID` is *not* a stable physical-camera
  identifier across frames — the **image name** (`camNN.png`) is. The converter relies on the
  name for identity and remaps camera_ids per frame (85 = 1 + 21×4 total), surfacing the
  physical grouping in `time_meta.devices` (each `camNN` device bound to its exclusive
  per-frame camera_ids; frame-0's shared id is left unbound to keep `validate` uniqueness).
- This is exactly the OPEN-1 situation and validated that design against real data.

### E2E result (5 frames)
- Merged model: 85 cameras, 105 images (5×21), 37 887 points (Σ per-frame, no dedup).
- Sidecars: `times` covers all 105 images; `points_t` covers all 37 887 points; timestamps
  synthetic uniform at 1/30 s (`clock_domain: synthetic_uniform`), range 0 … 133 333 332 ns.
- `validate_full` → exit 0; `colmap_io` and pycolmap agree; the verification suite
  (`conformance/test_real_per_frame.py`, env-gated) passes all checks including an exact
  point-multiset comparison.
