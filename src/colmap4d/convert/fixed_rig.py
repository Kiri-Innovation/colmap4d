"""Convert fixed-rig multi-camera captures to colmap4d using pre-calibrated poses.

For fixed camera arrays where intrinsics and extrinsics are calibrated once and reused,
this converter takes a rig calibration file and timestamped images, and produces a
colmap4d model where camera poses are from the calibration (not re-estimated).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from colmap4d import sidecar
from colmap4d.rig_calibration import RigCalibration


@dataclass
class TimestampedImage:
    """Image with timestamp."""

    camera_name: str  # e.g., "cam1"
    frame_index: int  # Frame number in sequence
    timestamp_ns: int  # Nanosecond timestamp
    image_path: Path | None = None  # Path to image file (optional)


def _fmt(x: float) -> str:
    """Format float for COLMAP text files."""
    return f"{x:.12g}"


def _write_cameras_txt(path: Path, calibration: RigCalibration) -> dict[str, int]:
    """Write cameras.txt from rig calibration.

    Returns:
        Mapping from camera_name to CAMERA_ID
    """
    lines = ["# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n"]

    camera_id_map = {}
    for cam_id, (name, cam) in enumerate(sorted(calibration.cameras.items()), start=1):
        intr = cam.intrinsics
        lines.append(
            f"{cam_id} {intr.model} {intr.width} {intr.height} "
            + " ".join(_fmt(p) for p in intr.params)
            + "\n"
        )
        camera_id_map[name] = cam_id

    lines.insert(1, f"# Number of cameras: {len(camera_id_map)}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return camera_id_map


def _write_images_txt(
    path: Path,
    images: list[TimestampedImage],
    calibration: RigCalibration,
    camera_id_map: dict[str, int],
) -> dict[int, int]:
    """Write images.txt with poses from rig calibration.

    Returns:
        Mapping from IMAGE_ID to timestamp_ns
    """
    lines = [
        "# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n",
        "#   POINTS2D[] as (X, Y, POINT3D_ID)\n",
    ]

    times = {}
    image_id = 1

    for img in sorted(images, key=lambda x: (x.frame_index, x.camera_name)):
        cam_name = img.camera_name
        if cam_name not in calibration.cameras:
            raise ValueError(f"Camera {cam_name} not in rig calibration")

        cam = calibration.cameras[cam_name]
        ext = cam.extrinsics
        cam_id = camera_id_map[cam_name]

        # Image name includes frame index for clarity
        image_name = f"frame_{img.frame_index:04d}/{cam_name}.jpg"

        lines.append(
            f"{image_id} {_fmt(ext.qw)} {_fmt(ext.qx)} {_fmt(ext.qy)} {_fmt(ext.qz)} "
            f"{_fmt(ext.tx)} {_fmt(ext.ty)} {_fmt(ext.tz)} {cam_id} {image_name}\n"
        )
        lines.append("\n")  # Empty POINTS2D line (no observations yet)

        times[image_id] = img.timestamp_ns
        image_id += 1

    lines.insert(2, f"# Number of images: {len(images)}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return times


def _write_points3d_txt(path: Path) -> None:
    """Write empty points3D.txt (placeholder for now)."""
    lines = [
        "# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n",
        "# Number of points: 0\n",
    ]
    path.write_text("".join(lines), encoding="utf-8")


def convert_fixed_rig_to_colmap4d(
    calibration_path: Path,
    images: list[TimestampedImage],
    output_dir: Path,
    clock_domain: str = "utc_ntp",
) -> None:
    """Convert fixed-rig captures to colmap4d using pre-calibrated poses.

    Args:
        calibration_path: Path to rig calibration JSON
        images: List of timestamped images
        output_dir: Output directory for colmap4d model
        clock_domain: Time synchronization method (e.g., "utc_ntp")
    """
    # Load calibration
    calibration = RigCalibration.load(calibration_path)
    print(f"📐 Loaded rig calibration: {calibration.rig_id}")
    print(f"   Calibrated: {calibration.calibrated_at}")
    print(f"   Cameras: {len(calibration.cameras)}")

    # Validate images match calibration
    image_cameras = {img.camera_name for img in images}
    calib_cameras = set(calibration.cameras.keys())
    missing = calib_cameras - image_cameras
    if missing:
        print(f"⚠️  Warning: calibration has cameras not in images: {missing}")

    extra = image_cameras - calib_cameras
    if extra:
        raise ValueError(f"Images contain cameras not in calibration: {extra}")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write COLMAP base model with fixed poses (binary format to handle empty POINTS2D)
    print(f"\n📝 Writing COLMAP model to {output_dir}...")
    camera_id_map = _write_cameras_txt(output_dir / "cameras.txt", calibration)

    # Build images using colmap_io structures
    from colmap4d.colmap_io import Image as ColmapImage
    from colmap4d.colmap_io import write_images_bin

    colmap_images = {}
    times_dict = {}
    image_id = 1

    for img in sorted(images, key=lambda x: (x.frame_index, x.camera_name)):
        cam_name = img.camera_name
        if cam_name not in calibration.cameras:
            raise ValueError(f"Camera {cam_name} not in rig calibration")

        cam = calibration.cameras[cam_name]
        ext = cam.extrinsics
        cam_id = camera_id_map[cam_name]
        image_name = f"frame_{img.frame_index:04d}/{cam_name}.jpg"

        # ColmapImage with empty POINTS2D
        colmap_images[image_id] = ColmapImage(
            id=image_id,
            name=image_name,
            camera_id=cam_id,
            qvec=[ext.qw, ext.qx, ext.qy, ext.qz],
            tvec=[ext.tx, ext.ty, ext.tz],
            xys=[],
            point3D_ids=[],
        )
        times_dict[image_id] = img.timestamp_ns
        image_id += 1

    write_images_bin(output_dir / "images.bin", colmap_images)
    image_times = times_dict

    _write_points3d_txt(output_dir / "points3D.txt")

    # Write colmap4d sidecars
    print("📝 Writing colmap4d sidecars...")

    # times.txt
    sidecar.write_times_txt(output_dir / "times.txt", image_times)

    # time_meta.json
    meta = {
        "colmap4d_spec": "1.0",
        "time_convention": "mid_exposure",
        "clock_domain": clock_domain,
        "rig_id": calibration.rig_id,
        "rig_calibration_source": str(calibration_path),
        "note": "Fixed rig - poses from pre-calibration, not SfM",
    }
    sidecar.write_time_meta(output_dir / "time_meta.json", meta)

    print("\n✅ Conversion complete!")
    print(f"   Output: {output_dir}")
    print(f"   Images: {len(images)}")
    print(f"   Frames: {len(set(img.frame_index for img in images))}")
    print(f"   Cameras per frame: {len(image_cameras)}")
