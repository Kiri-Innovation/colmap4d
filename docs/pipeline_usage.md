# Fixed-Rig Pipeline Usage

Quick guide for processing fixed-rig multi-camera video captures into colmap4d format.

## Prerequisites

- Python 3.10+ with colmap4d package installed (`pip install -e .`)
- COLMAP binary
- Input: shoot directory with multi-camera videos and timestamp sidecars

## One-Command Pipeline

```bash
python scripts/run_pipeline.py \
    --shoot-dir /path/to/shoot_YYYYMMDD \
    --output-dir /path/to/output \
    --rig-id your-rig-identifier \
    [--rig-calibration /path/to/existing/calibration.json] \
    [--resolution 1920x1440] \
    [--jpeg-quality 85] \
    [--colmap /path/to/colmap]
```

### Input Directory Structure

The shoot directory must contain:

```
shoot_YYYYMMDD/
├── manifest.json                    # Camera list and metadata
├── <camera-uuid-1>/
│   ├── video.mp4                    # Per-camera video
│   └── timestamps.jsonl             # Frame timestamps + metadata
├── <camera-uuid-2>/
│   ├── video.mp4
│   └── timestamps.jsonl
└── ...
```

### Output Directory Structure

```
output/
├── rig_calibration.json             # Camera intrinsics + extrinsics
├── calibration_frames/              # Frames used for calibration
│   ├── <camera-uuid-1>.jpg
│   ├── <camera-uuid-2>.jpg
│   └── ...
├── colmap4d_output/                 # Final colmap4d model
│   ├── cameras.txt                  # Camera intrinsics
│   ├── images.bin                   # Image poses + timestamps
│   ├── points3D.bin                 # Static 3D points (from calibration)
│   ├── points3D.txt
│   ├── times.txt                    # Per-image timestamps (nanoseconds)
│   ├── points_t.txt                 # Point timestamps (empty = static)
│   ├── time_meta.json               # Timestamp metadata
│   └── images/                      # Extracted frames
│       ├── frame_0000/
│       │   ├── <camera-uuid-1>.jpg
│       │   ├── <camera-uuid-2>.jpg
│       │   └── ...
│       ├── frame_0001/
│       └── ...
└── PIPELINE_REPORT.md               # Execution summary
```

## Pipeline Steps

The pipeline automatically performs:

1. **Extract calibration frames** (if needed)
   - Extracts one frame per camera (default: frame 100)
   - Used for COLMAP SfM calibration

2. **Calibrate rig** (or reuse existing)
   - Runs COLMAP SfM to extract intrinsics + extrinsics
   - Reuses existing calibration if `--rig-calibration` matches `--rig-id`
   - Saves to `rig_calibration.json`

3. **Parse timestamps**
   - Reads all camera timestamp sidecars
   - Converts to epoch time (mid-exposure)

4. **Convert to colmap4d**
   - Writes sparse model (cameras, images, points)
   - Includes static 3D points from calibration (marked as temporally-unbounded)
   - Writes timestamp sidecars (times.txt, points_t.txt, time_meta.json)

5. **Extract frames with timestamp matching**
   - Uses corrected timestamp matching algorithm (see below)
   - Rebuilds model to ensure strict 1:1 correspondence
   - Typical match rate: 95-96% (4-5% lost to encoder drops)

6. **Validate**
   - Checks file existence
   - Verifies times.txt ↔ images.bin ↔ files consistency
   - Reports per-camera statistics

## Timestamp Matching Details

The frame extraction uses **timestamp matching with offset correction**, NOT simple index mapping:

- **Why not "Nth sidecar → Nth frame"?**  
  Sidecar records all sensor frames (including encoder drops), but video only contains encoded frames.
  
- **How it works:**
  1. Read actual PTS timestamps from video frames
  2. Convert to absolute time using `firstTimestampNs` anchor from sidecar footer
  3. Estimate per-camera systematic timing offset (median of 100 sample frames)
  4. Match each sidecar timestamp to nearest video frame (threshold: 5ms)
  5. Rebuild model to include only successfully matched frames

- **Result:**  
  100% of matched frames have <1ms timestamp error (typical: 0.2ms)

See `TIMESTAMP_MATCHING_REPORT.md` in shoot output directories for detailed analysis.

## Reusing Rig Calibration

If you've already calibrated a rig and the physical setup hasn't changed:

```bash
python scripts/run_pipeline.py \
    --shoot-dir /path/to/new_shoot \
    --output-dir /path/to/output \
    --rig-id same-rig-identifier \
    --rig-calibration /path/to/previous/rig_calibration.json \
    ...
```

The pipeline will:
- Skip recalibration if `rig_id` matches
- Still extract calibration frames (needed for 3D points)
- Reuse camera poses and intrinsics

## Common Issues

### "ERROR: manifest.json not found"

Ensure the shoot directory contains `manifest.json` with camera list:

```json
{
  "cameras": [
    {"name": "<camera-uuid>", "sidecar": "timestamps.jsonl"},
    ...
  ]
}
```

### "COLMAP reconstruction failed"

- Check calibration frames have sufficient overlap
- Try adjusting COLMAP parameters in `calibrate_rig()`
- Increase `--SiftExtraction.max_num_features` for sparse overlap

### Low frame match rate (<90%)

- Normal: 95-96% is typical (encoder drops 4-5% of frames)
- If much lower: check video/sidecar integrity
- See extraction log for per-camera offset estimates

## For New Captures

1. Record videos with fixed-rig setup
2. Export shoot directory with videos + timestamp sidecars
3. Run pipeline with unique `--rig-id`
4. For subsequent captures with same rig: reuse calibration

## Technical Details

- **Coordinate system:** COLMAP convention (cam_from_world)
- **Timestamp convention:** mid-exposure, nanoseconds since epoch
- **Clock domain:** UTC (NTP-synchronized)
- **Static points:** Marked as temporally-unbounded (empty points_t.txt)
- **Image format:** JPEG (default q=85), resizable via `--resolution`

## See Also

- `docs/converters.md` - Full converter documentation
- `scripts/extract_frames.py` - Frame extraction details
- `scripts/calibrate_rig.py` - Calibration standalone tool
