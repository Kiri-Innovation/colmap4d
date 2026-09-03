"""Convert a per-frame COLMAP capture into a single colmap4d model.

Input: an ordered list of per-frame COLMAP sparse models (``colmap_0/…``, ``colmap_1/…``),
each an independent reconstruction of the *same rig at one time instant*. Output: one standard
COLMAP model + colmap4d sidecars, where each image and point carries that frame's timestamp.

Design choices (each traces to a white-paper principle; the contentious one is flagged):

* **Points: per-frame independent, NO cross-frame dedup (default).** White paper §3.3: a
  per-frame import honestly samples a persistent structure as one point *per frame*. Every
  frame's points are kept and remapped to globally-unique ids; each gets its frame's time.
  Cross-frame dedup would fabricate correspondences COLMAP never computed and collapse the
  honest samples into a "conclusion" — it violates "store observations, not conclusions"
  (§2). Per the author (white paper Q3/§3.3, OPEN-6 settled), cross-frame dedup is a derived
  view / downstream optimization and is **out of scope for v1**: ``dedup_points=True`` stays
  reserved and is refused.

* **Cameras: per-frame independent, remapped (lossless).** Real captures reassign COLMAP
  ``CAMERA_ID`` per frame (one frame may share a single intrinsic across all physical cameras,
  another may give each its own), so intrinsics are kept as-is per frame rather than reconciled.
  The stable physical-camera identity is the **image name**, preserved as ``frame_XXXX/<name>``
  and surfaced in ``time_meta.devices`` keyed by name.

* **Timestamps: frame instant.** All images in one frame share the frame's time, so every
  point's per-track-centroid time is exactly that instant (II.1 degenerates cleanly). Real
  timestamps are used when supplied; otherwise synthesized as ``frame_index * frame_interval_ns``
  and marked synthetic in ``time_meta`` (clock_domain ``synthetic_frame_index``).

Reads base models via pycolmap (optional dependency); writes COLMAP text + text sidecars.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from colmap4d import model, sidecar

# 30 fps default when no real timestamps are provided.
DEFAULT_FRAME_INTERVAL_NS = 33_333_333


@dataclass
class ConversionResult:
    out_dir: Path
    num_frames: int
    num_cameras: int
    num_images: int
    num_points: int
    frame_interval_ns: int
    synthetic_times: bool


def _fmt(x: float) -> str:
    return f"{x:.12g}"


def _write_cameras_txt(path: Path, cams: list[tuple]) -> None:
    lines = ["# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n", f"# Number of cameras: {len(cams)}\n"]
    for cid, model_name, w, h, params in cams:
        lines.append(f"{cid} {model_name} {w} {h} " + " ".join(_fmt(p) for p in params) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def _write_images_txt(path: Path, images: dict, obs: dict) -> None:
    lines = [
        "# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n",
        "#   POINTS2D[] as (X, Y, POINT3D_ID)\n",
        f"# Number of images: {len(images)}\n",
    ]
    for iid in sorted(images):
        im = images[iid]
        lines.append(
            f"{iid} {_fmt(im['qw'])} {_fmt(im['qx'])} {_fmt(im['qy'])} {_fmt(im['qz'])} "
            f"{_fmt(im['tx'])} {_fmt(im['ty'])} {_fmt(im['tz'])} {im['cam']} {im['name']}\n"
        )
        pts = obs.get(iid, [])
        lines.append(" ".join(f"{_fmt(x)} {_fmt(y)} {pid}" for x, y, pid in pts) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def _write_points3d_txt(path: Path, points: dict) -> None:
    lines = [
        "# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n",
        f"# Number of points: {len(points)}\n",
    ]
    for pid in sorted(points):
        p = points[pid]
        x, y, z = p["xyz"]
        r, g, b = p["rgb"]
        track = " ".join(f"{iid} {idx}" for iid, idx in p["track"])
        head = f"{pid} {_fmt(x)} {_fmt(y)} {_fmt(z)} {r} {g} {b} {_fmt(p['error'])}"
        lines.append(f"{head} {track}\n")
    path.write_text("".join(lines), encoding="utf-8")


def convert_per_frame_colmap(
    frame_dirs: list[str | Path],
    out_dir: str | Path,
    *,
    frame_times_ns: list[int] | None = None,
    frame_interval_ns: int = DEFAULT_FRAME_INTERVAL_NS,
    dedup_points: bool = False,
) -> ConversionResult:
    """Merge ``frame_dirs`` (ordered per-frame COLMAP models) into a colmap4d model at ``out_dir``.

    ``frame_times_ns`` gives an explicit timestamp per frame; when omitted, timestamps are
    synthesized as ``index * frame_interval_ns`` and flagged synthetic in ``time_meta``.
    """
    if dedup_points:
        raise NotImplementedError(
            "cross-frame point dedup is out of scope for v1 (OPEN-6, settled per white paper "
            "Q3/§3.3): per-frame imports keep independent xyzt samples; dedup is a derived "
            "view / downstream optimization. dedup_points is reserved and refused."
        )
    frame_dirs = [Path(d) for d in frame_dirs]
    if frame_times_ns is not None and len(frame_times_ns) != len(frame_dirs):
        raise ValueError("frame_times_ns must match frame_dirs length")

    cam_map: dict[tuple[int, int], int] = {}
    img_map: dict[tuple[int, int], int] = {}
    new_cameras: list[tuple] = []
    new_images: dict[int, dict] = {}
    new_points: dict[int, dict] = {}
    obs: dict[int, list] = defaultdict(list)
    times: dict[int, int] = {}
    points_t: dict[int, int] = {}
    name_to_camids: dict[str, set[int]] = defaultdict(set)
    camid_to_names: dict[int, set[str]] = defaultdict(set)

    next_cam = next_img = next_pt = 1
    for f, d in enumerate(frame_dirs):
        rec = model.read_reconstruction(d)
        t = frame_times_ns[f] if frame_times_ns is not None else f * frame_interval_ns

        for cid, cam in rec.cameras.items():
            cam_map[(f, int(cid))] = next_cam
            new_cameras.append(
                (next_cam, cam.model.name, cam.width, cam.height, [float(p) for p in cam.params])
            )
            next_cam += 1

        for iid, im in rec.images.items():
            new_img = next_img
            next_img += 1
            img_map[(f, int(iid))] = new_img
            cfw = im.cam_from_world()
            q = cfw.rotation.quat  # [x, y, z, w]
            tr = cfw.translation
            new_cam = cam_map[(f, int(im.camera_id))]
            stem = Path(im.name).stem
            new_images[new_img] = {
                "qw": float(q[3]),
                "qx": float(q[0]),
                "qy": float(q[1]),
                "qz": float(q[2]),
                "tx": float(tr[0]),
                "ty": float(tr[1]),
                "tz": float(tr[2]),
                "cam": new_cam,
                "name": f"frame_{f:04d}/{im.name}",
            }
            times[new_img] = t
            name_to_camids[stem].add(new_cam)
            camid_to_names[new_cam].add(stem)

        for pt in rec.points3D.values():
            new_pt = next_pt
            next_pt += 1
            track = []
            for el in pt.track.elements:
                new_img = img_map[(f, int(el.image_id))]
                xy = rec.images[el.image_id].points2D[el.point2D_idx].xy
                idx = len(obs[new_img])
                obs[new_img].append((float(xy[0]), float(xy[1]), new_pt))
                track.append((new_img, idx))
            new_points[new_pt] = {
                "xyz": [float(v) for v in pt.xyz],
                "rgb": [int(v) for v in pt.color],
                "error": float(pt.error),
                "track": track,
            }
            points_t[new_pt] = t

    # Device provenance: one device per physical camera name; attach camera_ids only where a
    # global CAMERA_ID maps unambiguously to a single name (keeps validate's uniqueness rule).
    devices = {}
    for stem in sorted(name_to_camids):
        exclusive = sorted(c for c in name_to_camids[stem] if len(camid_to_names[c]) == 1)
        dev = {"note": "physical camera identified by image name across frames"}
        if exclusive:
            dev["camera_ids"] = exclusive
        devices[stem] = dev

    synthetic = frame_times_ns is None
    ts_note = "synthetic (frame_index * frame_interval_ns)" if synthetic else "provided"
    pt_policy = "frame_instant; per-frame independent points, no cross-frame dedup"
    # Synthetic timestamps are uniform frame_index * interval — the degenerate case of a
    # software-clocked capture where the real per-image offsets were not recorded.
    time_meta = {
        "colmap4d_spec": "0.2-draft",
        "time_convention": "mid_exposure",
        "time_unit": "ns",
        "clock_domain": "synthetic_uniform" if synthetic else "provided",
        "devices": devices,
        "conversion": {
            "source": "per_frame_colmap",
            "num_frames": len(frame_dirs),
            "frame_interval_ns": frame_interval_ns,
            "timestamps": ts_note,
            "point_time_policy": pt_policy,
        },
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_cameras_txt(out / "cameras.txt", new_cameras)
    _write_images_txt(out / "images.txt", new_images, obs)
    _write_points3d_txt(out / "points3D.txt", new_points)
    sidecar.write_times_txt(out / sidecar.TIMES_TXT, times)
    sidecar.write_points_t_txt(out / sidecar.POINTS_T_TXT, points_t)
    sidecar.write_time_meta(out / sidecar.TIME_META_JSON, time_meta)

    return ConversionResult(
        out_dir=out,
        num_frames=len(frame_dirs),
        num_cameras=len(new_cameras),
        num_images=len(new_images),
        num_points=len(new_points),
        frame_interval_ns=frame_interval_ns,
        synthetic_times=synthetic,
    )
