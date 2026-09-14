#!/usr/bin/env python3
"""Calibrate a fixed multi-camera rig using COLMAP SfM.

Input: N images (one per camera, all from the same time instant)
Output: Rig calibration JSON file with intrinsics + extrinsics for each camera

Usage:
    python calibrate_rig.py \
        --images /path/to/calib-frames/*.png \
        --rig-id "studio-rig-01" \
        --output calibration.json \
        --colmap /path/to/colmap

The calibration captures camera poses (cam_from_world) and intrinsics that can be reused
across multiple captures as long as the rig remains fixed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import pycolmap
except ImportError:
    print("ERROR: pycolmap not installed. Install with: pip install 'colmap4d[model]'")
    sys.exit(1)

# Import from the colmap4d package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from colmap4d.rig_calibration import (
    CalibratedCamera,
    CameraExtrinsics,
    CameraIntrinsics,
    RigCalibration,
    validate_rig_calibration,
)


def run_colmap_sfm(
    image_paths: list[Path], colmap_bin: str, work_dir: Path
) -> pycolmap.Reconstruction:
    """Run COLMAP SfM pipeline on calibration images.

    Args:
        image_paths: List of input image paths
        colmap_bin: Path to COLMAP binary
        work_dir: Working directory for COLMAP

    Returns:
        COLMAP reconstruction object
    """
    # Setup directory structure
    images_dir = work_dir / "images"
    database_path = work_dir / "database" / "database.db"
    sparse_dir = work_dir / "sparse"

    images_dir.mkdir(parents=True)
    database_path.parent.mkdir(parents=True)
    sparse_dir.mkdir(parents=True)

    # Copy images to work directory
    for img_path in image_paths:
        shutil.copy(img_path, images_dir / img_path.name)

    print(f"📸 Running COLMAP SfM on {len(image_paths)} images...")

    # Step 1: Feature extraction
    print("  [1/3] Extracting features...")
    subprocess.run(
        [
            colmap_bin,
            "feature_extractor",
            "--database_path",
            str(database_path),
            "--image_path",
            str(images_dir),
            "--ImageReader.single_camera",
            "1",  # Shared intrinsics (same camera model)
            "--SiftExtraction.max_image_size",
            "2000",  # Downscale for better matching stability
        ],
        check=True,
        capture_output=True,
    )

    # Step 2: Feature matching
    print("  [2/3] Matching features...")
    subprocess.run(
        [
            colmap_bin,
            "exhaustive_matcher",
            "--database_path",
            str(database_path),
            "--FeatureMatching.guided_matching",
            "1",  # Guided matching for wide baseline
        ],
        check=True,
        capture_output=True,
    )

    # Step 3: Sparse reconstruction
    print("  [3/3] Running sparse reconstruction...")
    subprocess.run(
        [
            colmap_bin,
            "mapper",
            "--database_path",
            str(database_path),
            "--image_path",
            str(images_dir),
            "--output_path",
            str(sparse_dir),
        ],
        check=True,
        capture_output=True,
    )

    # Load the reconstruction
    model_path = sparse_dir / "0"
    if not model_path.exists():
        raise RuntimeError("COLMAP reconstruction failed: no model 0 created")

    reconstruction = pycolmap.Reconstruction(model_path)
    return reconstruction


def extract_calibration(
    reconstruction: pycolmap.Reconstruction, rig_id: str, notes: str | None = None
) -> RigCalibration:
    """Extract rig calibration from COLMAP reconstruction.

    Args:
        reconstruction: COLMAP reconstruction object
        rig_id: Unique identifier for this rig
        notes: Optional calibration notes

    Returns:
        RigCalibration object
    """
    cameras = {}

    # Extract camera parameters
    for _img_id, img in reconstruction.images.items():
        cam = reconstruction.cameras[img.camera_id]

        # Get camera name from image filename (e.g., "cam1.png" -> "cam1")
        cam_name = Path(img.name).stem

        # Extract intrinsics
        intrinsics = CameraIntrinsics(
            model=cam.model.name,
            width=cam.width,
            height=cam.height,
            params=[float(p) for p in cam.params],
        )

        # Extract extrinsics (cam_from_world)
        cfw = img.cam_from_world()
        q = cfw.rotation.quat  # [x, y, z, w] in pycolmap
        t = cfw.translation

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
        num_visible = sum(1 for p in img.points2D if p.has_point3D())

        cameras[cam_name] = CalibratedCamera(
            name=cam_name,
            serial=None,  # Could be extracted from EXIF if available
            intrinsics=intrinsics,
            extrinsics=extrinsics,
            num_3d_points_visible=num_visible,
        )

    # Compute quality metrics
    num_points = len(reconstruction.points3D)
    errors = [pt.error for pt in reconstruction.points3D.values()]
    mean_error = sum(errors) / len(errors) if errors else 0.0
    track_lengths = [pt.track.length() for pt in reconstruction.points3D.values()]
    mean_track_length = sum(track_lengths) / len(track_lengths) if track_lengths else 0.0

    quality = {
        "num_3d_points": num_points,
        "mean_reproj_error_px": mean_error,
        "mean_track_length": mean_track_length,
        "num_registered_images": reconstruction.num_reg_images(),
    }

    calibrated_at = datetime.now(timezone.utc).isoformat()

    return RigCalibration(
        rig_id=rig_id,
        calibrated_at=calibrated_at,
        cameras=cameras,
        quality=quality,
        notes=notes,
    )


def calibrate_rig(
    image_paths: list[Path],
    rig_id: str,
    output_path: Path,
    colmap_bin: str = "colmap",
    notes: str | None = None,
) -> RigCalibration:
    """Calibrate a fixed camera rig from a set of calibration images.

    Args:
        image_paths: List of calibration image paths (one per camera)
        rig_id: Unique identifier for this rig
        output_path: Output path for calibration JSON
        colmap_bin: Path to COLMAP binary
        notes: Optional calibration notes

    Returns:
        RigCalibration object
    """
    print(f"\n{'=' * 80}")
    print("Fixed Camera Rig Calibration")
    print(f"{'=' * 80}")
    print(f"Rig ID: {rig_id}")
    print(f"Images: {len(image_paths)}")
    print(f"Output: {output_path}")
    print()

    # Create temporary working directory
    with tempfile.TemporaryDirectory(prefix="colmap_calib_") as tmp_dir:
        work_dir = Path(tmp_dir)

        # Run COLMAP SfM
        reconstruction = run_colmap_sfm(image_paths, colmap_bin, work_dir)

        print("\n✅ SfM reconstruction successful!")
        print(f"   Registered images: {reconstruction.num_reg_images()}")
        print(f"   3D points: {len(reconstruction.points3D)}")

        # Extract calibration
        calibration = extract_calibration(reconstruction, rig_id, notes)

    # Validate
    print("\n🔍 Validating calibration...")
    errors = validate_rig_calibration(calibration)
    if errors:
        print("⚠️  Validation warnings:")
        for err in errors:
            print(f"   - {err}")

    # Save
    calibration.save(output_path)
    print(f"\n💾 Calibration saved to: {output_path}")

    # Print summary
    print("\n📊 Calibration Summary:")
    print(f"   Rig ID: {calibration.rig_id}")
    print(f"   Calibrated at: {calibration.calibrated_at}")
    print(f"   Cameras: {len(calibration.cameras)}")
    for name, cam in sorted(calibration.cameras.items()):
        fx = cam.intrinsics.params[0]
        print(
            f"      {name}: {cam.intrinsics.model}, fx={fx:.1f}px, "
            f"{cam.num_3d_points_visible} visible points"
        )
    print("   Quality:")
    print(f"      Mean reprojection error: {calibration.quality['mean_reproj_error_px']:.4f}px")
    print(f"      3D points: {calibration.quality['num_3d_points']}")
    print(f"      Mean track length: {calibration.quality['mean_track_length']:.2f}")
    print()

    return calibration


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate a fixed multi-camera rig using COLMAP SfM"
    )
    parser.add_argument(
        "--images",
        nargs="+",
        required=True,
        help="Calibration images (one per camera, same time instant)",
    )
    parser.add_argument("--rig-id", required=True, help="Unique identifier for this rig")
    parser.add_argument("--output", required=True, help="Output path for calibration JSON")
    parser.add_argument(
        "--colmap", default="colmap", help="Path to COLMAP binary (default: 'colmap')"
    )
    parser.add_argument("--notes", help="Optional calibration notes")

    args = parser.parse_args()

    # Resolve image paths
    image_paths = [Path(p).resolve() for p in args.images]
    for p in image_paths:
        if not p.exists():
            print(f"ERROR: Image not found: {p}")
            sys.exit(1)

    output_path = Path(args.output).resolve()

    try:
        calibrate_rig(
            image_paths=image_paths,
            rig_id=args.rig_id,
            output_path=output_path,
            colmap_bin=args.colmap,
            notes=args.notes,
        )
        print("✅ Calibration complete!")
    except Exception as e:
        print(f"\n❌ Calibration failed: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
