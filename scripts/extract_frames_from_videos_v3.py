#!/usr/bin/env python3
"""Extract frames from videos using timestamp matching with offset correction (v3 - FINAL).

Key improvements over v2:
- Per-camera systematic offset estimation (median of errors on sample frames)
- Offset correction before matching (achieves <1ms accuracy for 97%+ frames)
- Detailed error distribution reporting
- Strict 5ms threshold after offset correction

Background:
Video encoder introduces systematic timing offsets (~10-15ms typically) due to
processing delays. By estimating and correcting this offset per camera, we can
achieve sub-millisecond timestamp accuracy for the vast majority of frames.
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import concurrent.futures


def read_images_bin(images_bin_path: Path) -> List[Tuple[int, str]]:
    """Read (image_id, name) from images.bin."""
    images = []
    with open(images_bin_path, "rb") as f:
        num_images = struct.unpack("Q", f.read(8))[0]
        for _ in range(num_images):
            image_id = struct.unpack("I", f.read(4))[0]
            f.read(32 + 24 + 4)
            name_bytes = b""
            while True:
                c = f.read(1)
                if c == b"\x00":
                    break
                name_bytes += c
            images.append((image_id, name_bytes.decode("utf-8")))
            num_points2d = struct.unpack("Q", f.read(8))[0]
            f.read(num_points2d * 24)
    return images


def parse_image_name(name: str) -> Tuple[int, str]:
    """Parse 'frame_0007/camera_id.jpg' -> (7, 'camera_id')."""
    parts = name.split("/")
    frame_index = int(parts[0].replace("frame_", ""))
    camera_id = parts[1].replace(".jpg", "")
    return frame_index, camera_id


def read_video_pts(video_path: Path) -> List[float]:
    """Read PTS (seconds) for all video frames."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [float(line.strip()) for line in result.stdout.strip().split('\n') if line.strip()]


def parse_sidecar(sidecar_path: Path) -> Tuple[int, List[Tuple[int, int]]]:
    """Parse sidecar to get anchor and frame list.

    Returns:
        (first_timestamp_ns, [(frameIndex, timestampNs), ...])
    """
    lines = sidecar_path.read_text().strip().split('\n')
    footer = json.loads(lines[-1])
    if footer.get("type") != "footer":
        raise ValueError(f"Expected footer at end of {sidecar_path}")

    first_timestamp_ns = footer["firstTimestampNs"]
    frames = []
    for line in lines[1:-1]:
        data = json.loads(line)
        if data.get("type") == "frame":
            frames.append((data["frameIndex"], data["timestampNs"]))

    return first_timestamp_ns, frames


def estimate_offset(
    sidecar_frames: List[Tuple[int, int]],
    video_pts: List[float],
    first_timestamp_ns: int,
    sample_size: int = 100,
) -> int:
    """Estimate systematic offset by sampling frames.

    Returns:
        Median offset in nanoseconds (to add to video timestamps)
    """
    # Sample evenly distributed frames
    step = max(1, len(video_pts) // sample_size)
    sample_indices = list(range(0, len(video_pts), step))[:sample_size]

    # Build sidecar timestamp lookup
    sidecar_timestamps = [t for _, t in sidecar_frames]

    # Compute errors for sample
    errors = []
    for i in sample_indices:
        video_t_ns = first_timestamp_ns + int(video_pts[i] * 1e9)
        # Find closest sidecar timestamp
        closest_t = min(sidecar_timestamps, key=lambda t: abs(t - video_t_ns))
        error_ns = closest_t - video_t_ns
        errors.append(error_ns)

    # Return median (robust to outliers)
    errors.sort()
    return errors[len(errors) // 2]


def match_sidecar_to_video(
    sidecar_frames: List[Tuple[int, int]],
    video_pts: List[float],
    first_timestamp_ns: int,
    offset_ns: int,
    match_threshold_ns: int = 5_000_000,
) -> Tuple[Dict[int, Tuple[int, int]], List[Tuple[int, str]]]:
    """Match sidecar entries to video frames with offset correction.

    Returns:
        (matches, unmatched)
        matches: {frameIndex: (video_position, error_ns)}
        unmatched: [(frameIndex, reason), ...]
    """
    # Build video timestamp array (with offset correction)
    video_timestamps = [first_timestamp_ns + int(pts * 1e9) + offset_ns
                       for pts in video_pts]

    matches = {}
    unmatched = []

    for frame_idx, sidecar_t_ns in sidecar_frames:
        # Find closest video frame
        best_pos = min(range(len(video_timestamps)),
                      key=lambda i: abs(video_timestamps[i] - sidecar_t_ns))
        error_ns = abs(video_timestamps[best_pos] - sidecar_t_ns)

        if error_ns < match_threshold_ns:
            matches[frame_idx] = (best_pos, error_ns)
        else:
            reason = f"error_{error_ns/1e6:.1f}ms"
            unmatched.append((frame_idx, reason))

    return matches, unmatched


def extract_camera_frames(
    camera_id: str,
    video_path: Path,
    sidecar_path: Path,
    needed_frame_indices: List[int],
    output_base: Path,
    target_resolution: str,
    jpeg_quality: int,
) -> Dict[str, any]:
    """Extract frames with timestamp matching + offset correction."""

    # Read video and sidecar
    video_pts = read_video_pts(video_path)
    first_timestamp_ns, sidecar_frames = parse_sidecar(sidecar_path)

    # Filter to needed frames
    sidecar_frames_needed = [(idx, t) for idx, t in sidecar_frames
                              if idx in needed_frame_indices]

    # Estimate systematic offset
    offset_ns = estimate_offset(sidecar_frames, video_pts, first_timestamp_ns)

    # Match with offset correction
    matches, unmatched = match_sidecar_to_video(
        sidecar_frames_needed, video_pts, first_timestamp_ns, offset_ns
    )

    stats = {
        "camera_id": camera_id,
        "video_frames": len(video_pts),
        "sidecar_frames": len(sidecar_frames),
        "requested": len(needed_frame_indices),
        "matched": len(matches),
        "unmatched": len(unmatched),
        "offset_ms": offset_ns / 1e6,
        "match_errors_ns": [err for _, err in matches.values()],
        "unmatched_details": unmatched,
    }

    if not matches:
        return stats

    # Extract matched frames
    with tempfile.TemporaryDirectory(prefix=f"extract_{camera_id[:8]}_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        width, height = target_resolution.split('x')

        # Decode entire video
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-vf", f"scale={width}:{height}",
            "-q:v", str(100 - jpeg_quality),
            "-start_number", "0",
            str(tmp_path / "frame_%05d.jpg"),
            "-hide_banner", "-loglevel", "error",
        ]
        subprocess.run(cmd, check=True)

        # Copy matched frames
        for frame_idx, (video_pos, _) in matches.items():
            src = tmp_path / f"frame_{video_pos:05d}.jpg"
            dst_dir = output_base / f"frame_{frame_idx:04d}"
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f"{camera_id}.jpg"

            if src.exists():
                import shutil
                shutil.copy2(src, dst)
            else:
                stats["matched"] -= 1

    return stats


def rebuild_model(model_dir: Path, available_image_ids: set, output_dir: Path):
    """Rebuild model files to exclude images without extracted files."""
    print(f"\n🔄 Rebuilding model...")

    # Read original images.bin
    new_images = []
    with open(model_dir / "images.bin", "rb") as f:
        num_images = struct.unpack("Q", f.read(8))[0]
        for _ in range(num_images):
            image_id = struct.unpack("I", f.read(4))[0]
            qvec = struct.unpack("dddd", f.read(32))
            tvec = struct.unpack("ddd", f.read(24))
            camera_id = struct.unpack("I", f.read(4))[0]
            name_bytes = b""
            while True:
                c = f.read(1)
                if c == b"\x00":
                    break
                name_bytes += c
            name = name_bytes.decode("utf-8")
            num_points2d = struct.unpack("Q", f.read(8))[0]
            points2d = []
            for _ in range(num_points2d):
                x, y = struct.unpack("dd", f.read(16))
                p3d_id = struct.unpack("Q", f.read(8))[0]
                points2d.append((x, y, p3d_id))

            if image_id in available_image_ids:
                new_images.append({
                    "id": image_id, "qvec": qvec, "tvec": tvec,
                    "camera_id": camera_id, "name": name, "points2d": points2d,
                })

    # Write new images.bin
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "images.bin", "wb") as f:
        f.write(struct.pack("Q", len(new_images)))
        for img in new_images:
            f.write(struct.pack("I", img["id"]))
            f.write(struct.pack("dddd", *img["qvec"]))
            f.write(struct.pack("ddd", *img["tvec"]))
            f.write(struct.pack("I", img["camera_id"]))
            f.write(img["name"].encode("utf-8") + b"\x00")
            f.write(struct.pack("Q", len(img["points2d"])))
            for x, y, p3d_id in img["points2d"]:
                f.write(struct.pack("ddQ", x, y, p3d_id))

    # Rebuild times.txt
    if (model_dir / "times.txt").exists():
        times = {}
        with open(model_dir / "times.txt") as f:
            for line in f:
                if not line.startswith("#"):
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        img_id, t_ns = int(parts[0]), int(parts[1])
                        if img_id in available_image_ids:
                            times[img_id] = t_ns

        with open(output_dir / "times.txt", "w") as f:
            f.write("# colmap4d times: IMAGE_ID, T_NS (int64 ns)\n")
            f.write(f"# Number of images with a timestamp: {len(times)}\n")
            for img_id in sorted(times.keys()):
                f.write(f"{img_id} {times[img_id]}\n")

    # Copy other files
    for file in ["cameras.txt", "points3D.txt", "points_t.txt", "time_meta.json"]:
        if (model_dir / file).exists():
            import shutil
            shutil.copy2(model_dir / file, output_dir / file)

    print(f"   Original: {num_images} images")
    print(f"   Rebuilt: {len(new_images)} images")
    print(f"   Excluded: {num_images - len(new_images)}")


def main():
    parser = argparse.ArgumentParser(description="Extract frames with timestamp matching (v3)")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--shoot-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resolution", default="1920x1440")
    parser.add_argument("--jpeg-quality", type=int, default=85)
    parser.add_argument("--max-workers", type=int, default=4)

    args = parser.parse_args()

    images_bin = args.model_dir / "images.bin"
    if not images_bin.exists():
        print(f"ERROR: {images_bin} not found")
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("="*80)
    print("Frame Extraction v3 (Timestamp Matching + Offset Correction)")
    print("="*80)
    print(f"Model: {args.model_dir}")
    print()

    # Read model
    print("📖 Reading model...")
    images = read_images_bin(images_bin)
    camera_frames = defaultdict(list)
    image_id_to_name = {}

    for img_id, name in images:
        frame_idx, camera_id = parse_image_name(name)
        camera_frames[camera_id].append(frame_idx)
        image_id_to_name[img_id] = name

    print(f"   Model: {len(images)} images, {len(camera_frames)} cameras")

    # Extract frames
    print(f"\n🎬 Extracting frames...")
    all_stats = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {}
        for camera_id, frame_indices in camera_frames.items():
            video_path = args.shoot_dir / camera_id / "video.mp4"
            sidecar_path = args.shoot_dir / camera_id / "timestamps.jsonl"

            if not video_path.exists() or not sidecar_path.exists():
                print(f"  ✗ {camera_id[:8]}: missing files")
                continue

            print(f"  📹 {camera_id[:8]}: processing...")
            future = executor.submit(
                extract_camera_frames, camera_id, video_path, sidecar_path,
                frame_indices, args.output_dir, args.resolution, args.jpeg_quality
            )
            futures[future] = camera_id

        for future in concurrent.futures.as_completed(futures):
            camera_id = futures[future]
            try:
                stats = future.result()
                all_stats.append(stats)

                errors_ms = [e / 1e6 for e in stats["match_errors_ns"]]
                if errors_ms:
                    mean_err = sum(errors_ms) / len(errors_ms)
                    max_err = max(errors_ms)
                    print(f"  ✓ {camera_id[:8]}: {stats['matched']}/{stats['requested']} matched")
                    print(f"      Offset: {stats['offset_ms']:.3f}ms, Error: mean {mean_err:.3f}ms, max {max_err:.3f}ms")
                else:
                    print(f"  ⚠️  {camera_id[:8]}: 0 matches")
            except Exception as e:
                print(f"  ✗ {camera_id[:8]}: ERROR - {e}")

    # Summary
    print(f"\n{'='*80}")
    print(f"📊 Summary")
    print(f"{'='*80}")

    total_req = sum(s["requested"] for s in all_stats)
    total_matched = sum(s["matched"] for s in all_stats)
    total_unmatched = sum(s["unmatched"] for s in all_stats)

    print(f"Requested: {total_req}")
    print(f"Matched: {total_matched} ({100*total_matched/total_req:.1f}%)")
    print(f"Unmatched: {total_unmatched} ({100*total_unmatched/total_req:.1f}%)")

    # Error distribution
    all_errors_ms = []
    for s in all_stats:
        all_errors_ms.extend([e / 1e6 for e in s["match_errors_ns"]])

    if all_errors_ms:
        all_errors_ms.sort()
        print(f"\nMatch error distribution:")
        print(f"  Mean: {sum(all_errors_ms)/len(all_errors_ms):.3f}ms")
        print(f"  Median: {all_errors_ms[len(all_errors_ms)//2]:.3f}ms")
        print(f"  P95: {all_errors_ms[int(len(all_errors_ms)*0.95)]:.3f}ms")
        print(f"  Max: {max(all_errors_ms):.3f}ms")
        print(f"  <1ms: {sum(1 for e in all_errors_ms if e < 1.0)} ({100*sum(1 for e in all_errors_ms if e < 1.0)/len(all_errors_ms):.1f}%)")

    # Per-camera stats
    print(f"\nPer-camera details:")
    for s in sorted(all_stats, key=lambda x: x["camera_id"]):
        cam_short = s["camera_id"][:8]
        match_rate = 100 * s["matched"] / s["requested"] if s["requested"] > 0 else 0
        print(f"  {cam_short}: {s['matched']:3d}/{s['requested']:3d} ({match_rate:5.1f}%), "
              f"offset {s['offset_ms']:6.2f}ms, "
              f"video {s['video_frames']} frames, sidecar {s['sidecar_frames']} entries")

    # Rebuild model
    print()
    available_ids = set()
    for img_id, name in images:
        if (args.output_dir / name).exists():
            available_ids.add(img_id)

    rebuilt_dir = args.model_dir.parent / f"{args.model_dir.name}_rebuilt"
    rebuild_model(args.model_dir, available_ids, rebuilt_dir)

    # Final check
    total_size_mb = sum(f.stat().st_size for f in args.output_dir.rglob("*.jpg")) / (1024**2)
    print(f"\n✅ Extraction complete!")
    print(f"   Images extracted: {total_matched}")
    print(f"   Rebuilt model: {rebuilt_dir}")
    print(f"   Disk usage: {total_size_mb:.1f} MB ({total_size_mb/total_matched*1024:.1f} KB/frame)")


if __name__ == "__main__":
    main()
