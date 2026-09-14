#!/usr/bin/env python3
"""Unified pipeline for fixed-rig multi-camera video to colmap4d conversion.

This script automates the full workflow:
1. Extract calibration frames (if needed)
2. Calibrate rig (or reuse existing calibration if rig_id matches)
3. Convert to colmap4d (parse timestamps, write model + sidecars, include 3D points)
4. Extract video frames using timestamp matching with offset correction
5. Validate output (file existence, timestamp consistency, per-camera stats)

Usage:
    python run_pipeline.py \
        --shoot-dir /path/to/shoot_20260910 \
        --output-dir /path/to/output \
        --rig-id studio-rig-01 \
        [--rig-calibration /path/to/existing/calibration.json] \
        [--resolution 1920x1440] \
        [--jpeg-quality 85] \
        [--colmap /path/to/colmap]

The shoot directory should contain:
- manifest.json (camera list and metadata)
- <camera_id>/video.mp4 (per-camera videos)
- <camera_id>/timestamps.jsonl (per-camera timestamp sidecars)

Optional: If rig calibration already exists and matches the rig_id, provide
--rig-calibration to skip recalibration.

Output structure:
- rig_calibration.json (camera intrinsics + extrinsics)
- calibration_frames/ (extracted frames used for calibration)
- colmap4d_output/ (sparse model + sidecars + images/)
- PIPELINE_REPORT.md (summary of results)
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

# Add colmap4d package to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pycolmap

from colmap4d.colmap_io import Image, Point3D, write_images_bin, write_points3D_bin
from colmap4d.rig_calibration import (
    CalibratedCamera,
    CameraExtrinsics,
    CameraIntrinsics,
    RigCalibration,
)


def extract_calibration_frame(video_path: Path, output_path: Path, frame_number: int = 0) -> bool:
    """Extract a single frame from video for calibration."""
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vf",
        f"select='eq(n\\,{frame_number})'",
        "-vframes",
        "1",
        "-q:v",
        "2",
        str(output_path),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and output_path.exists()


def calibrate_rig(
    calib_frames_dir: Path,
    output_path: Path,
    rig_id: str,
    colmap_bin: str,
) -> RigCalibration:
    """Run COLMAP calibration on calibration frames."""
    print("  Running COLMAP calibration...")

    with tempfile.TemporaryDirectory(prefix="calib_") as tmp_dir:
        work_dir = Path(tmp_dir)
        database_path = work_dir / "database.db"
        sparse_dir = work_dir / "sparse"
        sparse_dir.mkdir(parents=True, exist_ok=True)

        # Feature extraction
        subprocess.run(
            [
                colmap_bin,
                "feature_extractor",
                "--database_path",
                str(database_path),
                "--image_path",
                str(calib_frames_dir),
                "--ImageReader.single_camera",
                "1",
                "--SiftExtraction.max_num_features",
                "32768",
            ],
            capture_output=True,
            check=True,
        )

        # Matching
        subprocess.run(
            [
                colmap_bin,
                "exhaustive_matcher",
                "--database_path",
                str(database_path),
                "--FeatureMatching.guided_matching",
                "1",
            ],
            capture_output=True,
            check=True,
        )

        # Reconstruction
        subprocess.run(
            [
                colmap_bin,
                "mapper",
                "--database_path",
                str(database_path),
                "--image_path",
                str(calib_frames_dir),
                "--output_path",
                str(sparse_dir),
            ],
            capture_output=True,
            check=True,
        )

        # Load reconstruction
        rec = pycolmap.Reconstruction(sparse_dir / "0")

        # Extract camera parameters
        cameras = {}
        for _image_id, image in rec.images.items():
            camera_name = image.name.replace(".jpg", "")
            camera_model = rec.cameras[image.camera_id]

            # Get pose using pycolmap API
            cfw = image.cam_from_world()
            q = cfw.rotation.quat  # [x, y, z, w]
            t = cfw.translation

            # Extract intrinsics
            intrinsics = CameraIntrinsics(
                model=camera_model.model.name,
                width=camera_model.width,
                height=camera_model.height,
                params=[float(p) for p in camera_model.params],
            )

            # Extract extrinsics (cam_from_world)
            extrinsics = CameraExtrinsics(
                qw=float(q[3]),  # COLMAP convention: qw first
                qx=float(q[0]),
                qy=float(q[1]),
                qz=float(q[2]),
                tx=float(t[0]),
                ty=float(t[1]),
                tz=float(t[2]),
            )

            # Count visible 3D points
            num_visible = sum(1 for p in image.points2D if p.has_point3D())

            cameras[camera_name] = CalibratedCamera(
                name=camera_name,
                serial=None,
                intrinsics=intrinsics,
                extrinsics=extrinsics,
                num_3d_points_visible=num_visible,
            )

        # Compute quality metrics
        num_points = len(rec.points3D)
        errors = [pt.error for pt in rec.points3D.values()]
        mean_error = sum(errors) / len(errors) if errors else 0.0
        track_lengths = [pt.track.length() for pt in rec.points3D.values()]
        mean_track_length = sum(track_lengths) / len(track_lengths) if track_lengths else 0.0

        quality = {
            "num_3d_points": num_points,
            "mean_reproj_error_px": mean_error,
            "mean_track_length": mean_track_length,
            "num_registered_images": rec.num_reg_images(),
        }

        from datetime import datetime, timezone

        calibrated_at = datetime.now(timezone.utc).isoformat()

        # Save calibration
        calibration = RigCalibration(
            rig_id=rig_id,
            calibrated_at=calibrated_at,
            cameras=cameras,
            quality=quality,
            notes=None,
        )
        calibration.save(output_path)

        print(f"  ✓ Calibrated {len(cameras)} cameras, {len(rec.points3D)} points")
        return calibration, rec


def parse_timestamps(manifest_path: Path, base_dir: Path) -> list[dict]:
    """Parse timestamps from all camera sidecars."""
    manifest = json.loads(manifest_path.read_text())
    timestamped_images = []

    for cam_entry in manifest["cameras"]:
        cam_name = cam_entry["name"]
        sidecar_path = base_dir / cam_name / cam_entry["sidecar"]

        lines = sidecar_path.read_text().strip().split("\n")
        header = json.loads(lines[0])
        clock_offset_ms = header.get("clockOffsetMs", 0)
        clock_offset_ns = clock_offset_ms * 1_000_000

        for line in lines[1:]:
            frame_data = json.loads(line)
            if frame_data.get("type") != "frame":
                continue

            frame_index = frame_data["frameIndex"]
            timestamp_realtime_ns = frame_data["timestampNs"]
            exposure_ns = frame_data.get("exposureNs", 0)

            # Convert to epoch time (mid-exposure)
            timestamp_epoch_ns = timestamp_realtime_ns + clock_offset_ns
            timestamp_mid_exposure_ns = timestamp_epoch_ns + exposure_ns // 2

            timestamped_images.append(
                {
                    "camera_name": cam_name,
                    "frame_index": frame_index,
                    "timestamp_ns": timestamp_mid_exposure_ns,
                }
            )

    return timestamped_images


def convert_to_colmap4d(
    calibration: RigCalibration,
    reconstruction: pycolmap.Reconstruction,
    timestamped_images: list[dict],
    output_dir: Path,
) -> None:
    """Convert rig calibration + timestamps to colmap4d format."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write cameras.txt (intrinsics)
    cameras_txt = output_dir / "cameras.txt"
    with open(cameras_txt, "w") as f:
        f.write("# Camera list with one line of data per camera:\n")
        f.write("#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")

        for cam_id, (_cam_name, cam_data) in enumerate(calibration.cameras.items(), start=1):
            params_str = " ".join(str(p) for p in cam_data.intrinsics.params)
            f.write(
                f"{cam_id} {cam_data.intrinsics.model} {cam_data.intrinsics.width} "
                f"{cam_data.intrinsics.height} {params_str}\n"
            )

    # Build camera_name → camera_id map
    camera_name_to_id = {name: idx + 1 for idx, name in enumerate(calibration.cameras.keys())}

    # Write images.bin (poses + timestamps)
    images = {}
    times = {}
    image_id = 1

    for img_data in timestamped_images:
        cam_name = img_data["camera_name"]
        frame_idx = img_data["frame_index"]
        t_ns = img_data["timestamp_ns"]

        cam_data = calibration.cameras[cam_name]
        camera_id = camera_name_to_id[cam_name]

        # Image name: frame_XXXX/<camera_id>.jpg
        name = f"frame_{frame_idx:04d}/{cam_name}.jpg"

        # Extract qvec and tvec from extrinsics
        qvec = [
            cam_data.extrinsics.qw,
            cam_data.extrinsics.qx,
            cam_data.extrinsics.qy,
            cam_data.extrinsics.qz,
        ]
        tvec = [cam_data.extrinsics.tx, cam_data.extrinsics.ty, cam_data.extrinsics.tz]

        images[image_id] = Image(
            id=image_id,
            qvec=qvec,
            tvec=tvec,
            camera_id=camera_id,
            name=name,
            xys=[],
            point3D_ids=[],
        )

        times[image_id] = t_ns
        image_id += 1

    write_images_bin(output_dir / "images.bin", images)

    # Write times.txt
    with open(output_dir / "times.txt", "w") as f:
        f.write("# Image timestamps (nanoseconds since epoch, mid-exposure)\n")
        f.write("# IMAGE_ID TIMESTAMP_NS\n")
        for img_id, t_ns in times.items():
            f.write(f"{img_id} {t_ns}\n")

    # Write 3D points (from calibration reconstruction)
    points3d = {}
    for point3d_id, point in reconstruction.points3D.items():
        points3d[point3d_id] = Point3D(
            id=point3d_id,
            xyz=point.xyz.tolist(),
            rgb=point.color.tolist(),
            error=point.error,
            track=[],  # Empty track for static points
        )

    write_points3D_bin(output_dir / "points3D.bin", points3d)

    # Write points3D.txt manually
    point_lines = [
        "# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n",
        f"# Number of points: {len(points3d)}\n",
    ]
    for pt_id in sorted(points3d):
        pt = points3d[pt_id]
        x, y, z = pt.xyz
        r, g, b = pt.rgb
        track_str = ""  # Empty track for static points
        line = (
            f"{pt_id} {x:.12g} {y:.12g} {z:.12g} {int(r)} {int(g)} {int(b)} "
            f"{pt.error:.12g} {track_str}\n"
        )
        point_lines.append(line)
    (output_dir / "points3D.txt").write_text("".join(point_lines))

    # Write points_t.txt (empty = temporally unbounded, visible at all times)
    with open(output_dir / "points_t.txt", "w") as f:
        f.write("# Point timestamps (empty = temporally unbounded)\n")
        f.write("# POINT3D_ID TIMESTAMP_NS\n")

    # Write time_meta.json
    with open(output_dir / "time_meta.json", "w") as f:
        json.dump(
            {
                "colmap4d_spec": "1.0",
                "time_convention": "mid_exposure",
                "clock_domain": "utc_ntp",
                "points_t_method": "unbounded_static",
            },
            f,
            indent=2,
        )

    print(f"  ✓ Wrote {len(images)} images, {len(points3d)} static points")


def extract_frames(
    model_dir: Path,
    shoot_dir: Path,
    output_images_dir: Path,
    resolution: str,
    jpeg_quality: int,
) -> dict:
    """Extract frames using the corrected timestamp matching script."""
    extract_script = Path(__file__).parent / "extract_frames.py"

    cmd = [
        sys.executable,
        str(extract_script),
        "--model-dir",
        str(model_dir),
        "--shoot-dir",
        str(shoot_dir),
        "--output-dir",
        str(output_images_dir),
        "--resolution",
        resolution,
        "--jpeg-quality",
        str(jpeg_quality),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("  ✗ Frame extraction failed:")
        print(result.stderr)
        raise RuntimeError("Frame extraction failed")

    # Parse stats from output (look for summary lines)
    stats = {}
    for line in result.stdout.split("\n"):
        if "Total frames:" in line:
            stats["total"] = int(line.split(":")[-1].strip())
        elif "Extracted:" in line:
            stats["extracted"] = int(line.split(":")[-1].strip())
        elif "Skipped:" in line:
            stats["skipped"] = int(line.split(":")[-1].strip())

    return stats


def validate_output(model_dir: Path, images_dir: Path) -> dict:
    """Validate model consistency."""
    # Read images.bin
    with open(model_dir / "images.bin", "rb") as f:
        num_images = struct.unpack("Q", f.read(8))[0]
        image_data = []
        for _ in range(num_images):
            image_id = struct.unpack("I", f.read(4))[0]
            f.read(32 + 24 + 4)
            name_bytes = b""
            while True:
                c = f.read(1)
                if c == b"\x00":
                    break
                name_bytes += c
            image_data.append((image_id, name_bytes.decode("utf-8")))
            num_points2d = struct.unpack("Q", f.read(8))[0]
            f.read(num_points2d * 24)

    # Read times.txt
    times = {}
    with open(model_dir / "times.txt") as f:
        for line in f:
            if not line.startswith("#"):
                parts = line.strip().split()
                if len(parts) >= 2:
                    times[int(parts[0])] = int(parts[1])

    # Check file existence
    existing = 0
    missing = []
    for _img_id, name in image_data:
        if (images_dir / name).exists():
            existing += 1
        else:
            missing.append(name)

    # Per-camera stats
    camera_stats = defaultdict(lambda: {"model": 0, "files": 0})
    for _img_id, name in image_data:
        cam_id = name.split("/")[1].replace(".jpg", "")
        camera_stats[cam_id]["model"] += 1
        if (images_dir / name).exists():
            camera_stats[cam_id]["files"] += 1

    return {
        "model_images": len(image_data),
        "times_entries": len(times),
        "existing_files": existing,
        "missing_files": len(missing),
        "camera_stats": dict(camera_stats),
        "consistent": (
            len(image_data) == len(times) == existing
            and len(missing) == 0
            and set(times.keys()) == set(img_id for img_id, _ in image_data)
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Fixed-rig multi-camera video to colmap4d pipeline"
    )
    parser.add_argument(
        "--shoot-dir",
        type=Path,
        required=True,
        help="Shoot directory containing manifest.json and camera subdirectories",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for pipeline results",
    )
    parser.add_argument(
        "--rig-id",
        required=True,
        help="Rig identifier (e.g., studio-rig-01)",
    )
    parser.add_argument(
        "--rig-calibration",
        type=Path,
        help="Existing rig calibration file (skip recalibration if matches rig-id)",
    )
    parser.add_argument(
        "--resolution",
        default="1920x1440",
        help="Target resolution for extracted frames (default: 1920x1440)",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=85,
        help="JPEG quality 1-100 (default: 85)",
    )
    parser.add_argument(
        "--colmap",
        default="colmap",
        help="Path to colmap binary (default: colmap)",
    )
    parser.add_argument(
        "--calibration-frame",
        type=int,
        default=100,
        help="Frame number to extract for calibration (default: 100)",
    )

    args = parser.parse_args()

    # Validate inputs
    manifest_path = args.shoot_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"ERROR: {manifest_path} not found")
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text())

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("Fixed-Rig Multi-Camera → colmap4d Pipeline")
    print("=" * 80)
    print(f"Shoot dir:    {args.shoot_dir}")
    print(f"Output dir:   {args.output_dir}")
    print(f"Rig ID:       {args.rig_id}")
    print(f"Resolution:   {args.resolution}")
    print(f"JPEG quality: {args.jpeg_quality}")
    print()

    start_time = time.time()

    # Step 1: Extract calibration frames (if needed)
    calib_frames_dir = args.output_dir / "calibration_frames"
    calib_json = args.output_dir / "rig_calibration.json"

    reused_calibration = False
    need_calibration_frames = True

    if args.rig_calibration and args.rig_calibration.exists():
        existing_calib = RigCalibration.load(args.rig_calibration)
        if existing_calib.rig_id == args.rig_id:
            print("✓ Reusing existing calibration (rig_id matches)")
            shutil.copy(args.rig_calibration, calib_json)
            calibration = existing_calib
            reconstruction = None
            reused_calibration = True
            # Still need calibration frames for 3D points extraction
            need_calibration_frames = True
        else:
            print(
                "⚠️  Existing calibration rig_id mismatch "
                f"({existing_calib.rig_id} != {args.rig_id})"
            )
            print("   Will recalibrate")

    if need_calibration_frames:
        print("\n1️⃣  Extracting calibration frames...")
        calib_frames_dir.mkdir(parents=True, exist_ok=True)

        for cam_entry in manifest["cameras"]:
            cam_name = cam_entry["name"]
            video_path = args.shoot_dir / cam_name / "video.mp4"
            output_frame = calib_frames_dir / f"{cam_name}.jpg"

            if not video_path.exists():
                print(f"  ✗ Video not found: {video_path}")
                continue

            if extract_calibration_frame(video_path, output_frame, args.calibration_frame):
                print(f"  ✓ {cam_name}")
            else:
                print(f"  ✗ {cam_name} (extraction failed)")

    # Step 2: Calibrate rig (only if not reusing)
    if not reused_calibration:
        print("\n2️⃣  Calibrating rig...")
        calibration, reconstruction = calibrate_rig(
            calib_frames_dir,
            calib_json,
            args.rig_id,
            args.colmap,
        )

    # Step 3: Parse timestamps
    print("\n3️⃣  Parsing timestamps...")
    timestamped_images = parse_timestamps(manifest_path, args.shoot_dir)
    print(f"  ✓ Parsed {len(timestamped_images)} timestamped images")

    # Step 4: Convert to colmap4d
    print("\n4️⃣  Converting to colmap4d...")
    model_dir = args.output_dir / "colmap4d_output"

    # If we reused calibration, we need to load the reconstruction for points
    if reused_calibration:
        # Run a quick reconstruction just to get 3D points
        print("  Re-running calibration to extract 3D points...")
        with tempfile.TemporaryDirectory(prefix="recon_") as tmp_dir:
            work_dir = Path(tmp_dir)
            database_path = work_dir / "database.db"
            sparse_dir = work_dir / "sparse"
            sparse_dir.mkdir(parents=True, exist_ok=True)

            subprocess.run(
                [
                    args.colmap,
                    "feature_extractor",
                    "--database_path",
                    str(database_path),
                    "--image_path",
                    str(calib_frames_dir),
                    "--ImageReader.single_camera",
                    "1",
                    "--SiftExtraction.max_num_features",
                    "32768",
                ],
                capture_output=True,
                check=True,
            )

            subprocess.run(
                [
                    args.colmap,
                    "exhaustive_matcher",
                    "--database_path",
                    str(database_path),
                    "--FeatureMatching.guided_matching",
                    "1",
                ],
                capture_output=True,
                check=True,
            )

            subprocess.run(
                [
                    args.colmap,
                    "mapper",
                    "--database_path",
                    str(database_path),
                    "--image_path",
                    str(calib_frames_dir),
                    "--output_path",
                    str(sparse_dir),
                ],
                capture_output=True,
                check=True,
            )

            reconstruction = pycolmap.Reconstruction(sparse_dir / "0")

    convert_to_colmap4d(calibration, reconstruction, timestamped_images, model_dir)

    # Step 5: Extract frames
    print("\n5️⃣  Extracting frames (timestamp matching with offset correction)...")
    images_dir = model_dir / "images"
    extract_stats = extract_frames(
        model_dir,
        args.shoot_dir,
        images_dir,
        args.resolution,
        args.jpeg_quality,
    )
    print(f"  ✓ Extracted {extract_stats.get('extracted', '?')} frames")

    # Move rebuilt model to replace original
    rebuilt_dir = model_dir.parent / f"{model_dir.name}_rebuilt"
    if rebuilt_dir.exists():
        print(f"  ✓ Moving rebuilt model to {model_dir}")
        # Remove old model files (but keep images dir)
        for f in model_dir.glob("*.bin"):
            f.unlink()
        for f in model_dir.glob("*.txt"):
            if f.name != "cameras.txt":  # Keep cameras.txt
                f.unlink()
        for f in model_dir.glob("*.json"):
            f.unlink()

        # Move rebuilt files
        for f in rebuilt_dir.glob("*"):
            if f.is_file():
                shutil.move(str(f), model_dir / f.name)

        # Remove rebuilt dir
        rebuilt_dir.rmdir()

    # Step 6: Validate
    print("\n6️⃣  Validating output...")
    validation = validate_output(model_dir, images_dir)

    if validation["consistent"]:
        print("  ✅ Model is fully consistent")
    else:
        print("  ⚠️  Consistency issues detected")

    print(f"     Model images: {validation['model_images']}")
    print(f"     times.txt entries: {validation['times_entries']}")
    print(f"     Existing files: {validation['existing_files']}")
    print(f"     Missing files: {validation['missing_files']}")

    # Step 7: Write summary report
    elapsed = time.time() - start_time

    report_path = args.output_dir / "PIPELINE_REPORT.md"
    with open(report_path, "w") as f:
        f.write("# Pipeline Execution Report\n\n")
        f.write(f"**Shoot:** {args.shoot_dir.name}\n\n")
        f.write(f"**Rig ID:** {args.rig_id}\n\n")
        f.write(f"**Execution time:** {elapsed:.1f}s\n\n")
        f.write("## Results\n\n")
        f.write(f"- **Model images:** {validation['model_images']}\n")
        f.write(f"- **Existing files:** {validation['existing_files']}\n")
        f.write(f"- **times.txt entries:** {validation['times_entries']}\n")
        f.write(f"- **Consistency:** {'✅ PASS' if validation['consistent'] else '⚠️ FAIL'}\n\n")
        f.write("## Per-Camera Stats\n\n")
        f.write("| Camera | Model | Files |\n")
        f.write("|--------|-------|-------|\n")
        for cam_id in sorted(validation["camera_stats"].keys()):
            stats = validation["camera_stats"][cam_id]
            f.write(f"| {cam_id[:8]} | {stats['model']} | {stats['files']} |\n")
        f.write("\n## Output Structure\n\n")
        f.write("```\n")
        f.write(f"{args.output_dir.name}/\n")
        f.write("├── rig_calibration.json\n")
        f.write("├── calibration_frames/\n")
        f.write("├── colmap4d_output/\n")
        f.write("│   ├── cameras.txt\n")
        f.write("│   ├── images.bin\n")
        f.write("│   ├── points3D.bin\n")
        f.write("│   ├── points3D.txt\n")
        f.write("│   ├── times.txt\n")
        f.write("│   ├── points_t.txt\n")
        f.write("│   ├── time_meta.json\n")
        f.write("│   └── images/\n")
        f.write("│       ├── frame_0000/\n")
        f.write("│       ├── frame_0001/\n")
        f.write("│       └── ...\n")
        f.write("└── PIPELINE_REPORT.md\n")
        f.write("```\n")

    print(f"\n{'=' * 80}")
    print(f"✅ Pipeline complete ({elapsed:.1f}s)")
    print(f"{'=' * 80}")
    print(f"\nOutput: {args.output_dir}")
    print(f"Report: {report_path}")

    if not validation["consistent"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
