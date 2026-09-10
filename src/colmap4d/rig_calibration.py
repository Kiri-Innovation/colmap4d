"""Fixed camera rig calibration for colmap4d.

Supports calibrating a fixed multi-camera array once and reusing poses across captures.
Calibration uses COLMAP SfM on a single set of frames (one per camera at the same instant).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CameraIntrinsics:
    """Camera intrinsic parameters."""

    model: str  # COLMAP camera model name (e.g., "SIMPLE_RADIAL", "PINHOLE")
    width: int
    height: int
    params: list[float]  # Model-specific parameters [fx, fy, cx, cy, ...distortion]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "width": self.width,
            "height": self.height,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CameraIntrinsics:
        return cls(model=d["model"], width=d["width"], height=d["height"], params=d["params"])


@dataclass
class CameraExtrinsics:
    """Camera extrinsic parameters (cam_from_world transform)."""

    qw: float  # Quaternion w (COLMAP convention: qw, qx, qy, qz)
    qx: float
    qy: float
    qz: float
    tx: float  # Translation
    ty: float
    tz: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "rotation_qwxyz": [self.qw, self.qx, self.qy, self.qz],
            "translation": [self.tx, self.ty, self.tz],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CameraExtrinsics:
        q = d["rotation_qwxyz"]
        t = d["translation"]
        return cls(qw=q[0], qx=q[1], qy=q[2], qz=q[3], tx=t[0], ty=t[1], tz=t[2])


@dataclass
class CalibratedCamera:
    """Single camera in the rig."""

    name: str  # Camera identifier (e.g., "cam1", serial number)
    serial: str | None  # Device serial number (optional)
    intrinsics: CameraIntrinsics
    extrinsics: CameraExtrinsics
    num_3d_points_visible: int  # Number of 3D points visible in calibration

    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "intrinsics": self.intrinsics.to_dict(),
            "extrinsics": self.extrinsics.to_dict(),
            "num_3d_points_visible": self.num_3d_points_visible,
        }
        if self.serial:
            d["serial"] = self.serial
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CalibratedCamera:
        return cls(
            name=d["name"],
            serial=d.get("serial"),
            intrinsics=CameraIntrinsics.from_dict(d["intrinsics"]),
            extrinsics=CameraExtrinsics.from_dict(d["extrinsics"]),
            num_3d_points_visible=d["num_3d_points_visible"],
        )


@dataclass
class RigCalibration:
    """Calibration for a fixed multi-camera rig."""

    rig_id: str  # Unique identifier for this rig
    calibrated_at: str  # ISO 8601 timestamp
    cameras: dict[str, CalibratedCamera]  # Keyed by camera name
    quality: dict[str, Any]  # Quality metrics (mean_reproj_error, num_3d_points, etc.)
    valid_until: str | None = None  # Optional expiration (ISO 8601)
    notes: str | None = None  # Optional calibration notes

    def to_dict(self) -> dict[str, Any]:
        d = {
            "rig_id": self.rig_id,
            "calibrated_at": self.calibrated_at,
            "cameras": {name: cam.to_dict() for name, cam in self.cameras.items()},
            "quality": self.quality,
        }
        if self.valid_until:
            d["valid_until"] = self.valid_until
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RigCalibration:
        return cls(
            rig_id=d["rig_id"],
            calibrated_at=d["calibrated_at"],
            cameras={name: CalibratedCamera.from_dict(c) for name, c in d["cameras"].items()},
            quality=d["quality"],
            valid_until=d.get("valid_until"),
            notes=d.get("notes"),
        )

    def save(self, path: str | Path) -> None:
        """Save calibration to JSON file."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> RigCalibration:
        """Load calibration from JSON file."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def validate_rig_calibration(
    calib: RigCalibration, required_cameras: set[str] | None = None
) -> list[str]:
    """Validate rig calibration.

    Args:
        calib: Rig calibration to validate
        required_cameras: Optional set of camera names that must be present

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    if not calib.rig_id:
        errors.append("rig_id is required")

    if not calib.cameras:
        errors.append("No cameras in calibration")

    if required_cameras:
        missing = required_cameras - set(calib.cameras.keys())
        if missing:
            errors.append(f"Missing required cameras: {sorted(missing)}")

    # Check quality metrics
    if "mean_reproj_error_px" not in calib.quality:
        errors.append("quality.mean_reproj_error_px is required")
    elif calib.quality["mean_reproj_error_px"] > 2.0:
        errors.append(
            f"High reprojection error: {calib.quality['mean_reproj_error_px']:.2f}px (threshold: 2.0px)"
        )

    if "num_3d_points" not in calib.quality:
        errors.append("quality.num_3d_points is required")
    elif calib.quality["num_3d_points"] < 50:
        errors.append(
            f"Too few 3D points: {calib.quality['num_3d_points']} (minimum: 50)"
        )

    return errors
