#!/usr/bin/env python3
"""DEPRECATED: This version is missing systematic offset correction.

BUG: Does timestamp matching but WITHOUT per-camera offset estimation, which causes
most frames to fail matching (only 13.6% match rate vs 95.6% in corrected version).

USE INSTEAD: extract_frames.py (timestamp matching WITH offset correction)

This version correctly implemented timestamp matching but failed to account for
systematic encoder timing offsets (~10-15ms per camera). The corrected version
estimates and corrects these offsets, achieving 95.6% match rate with <1ms errors.

---

Original description:
Extract frames from videos using timestamp matching (CORRECTED VERSION).

CRITICAL FIX: The previous version assumed "Nth sidecar entry = Nth video frame",
which breaks when the encoder drops frames. This version:
1. Reads actual PTS of each video frame
2. Matches sidecar entries to video frames by timestamp
3. Only extracts frames with timestamp match error < threshold
4. Rebuilds the colmap4d model to exclude entries without images

Background:
- Sidecar records every frame the sensor delivered (including gaps)
- Video only contains frames the encoder saved (can have dropped frames)
- firstTimestampNs in sidecar footer = absolute time of video frame 0
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
            f.read(32 + 24 + 4)  # Skip qvec, tvec, camera_id

            # Read name
            name_bytes = b""
            while True:
                c = f.read(1)
                if c == b"\x00":
                    break
                name_bytes += c
            name = name_bytes.decode("utf-8")
            images.append((image_id, name))

            # Skip points2D
            num_points2d = struct.unpack("Q", f.read(8))[0]
            f.read(num_points2d * 24)

    return images


def parse_image_name(name: str) -> Tuple[int, str]:
    """Parse image name to (frame_index, camera_id)."""
    parts = name.split("/")
    frame_index = int(parts[0].replace("frame_", ""))
    camera_id = parts[1].replace(".jpg", "")
    return frame_index, camera_id


def read_video_pts(video_path: Path) -> List[float]:
    """Read PTS (presentation timestamp in seconds) for all video frames."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time",
        "-of", "csv=p=0",
        str(video_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    pts_values = [float(line.strip()) for line in result.stdout.strip().split('\n') if line.strip()]
    return pts_values


def parse_sidecar(sidecar_path: Path) -> Tuple[int, List[Tuple[int, int, int]]]:
    """Parse sidecar to extract anchor timestamp and frame entries.

    Returns:
        (first_timestamp_ns, [(frameIndex, timestampNs_realtime, exposureNs), ...])
    """
    lines = sidecar_path.read_text().strip().split('\n')

    # Read header
    header = json.loads(lines[0])
    clock_offset_ns = header.get("clockOffsetMs", 0) * 1_000_000

    # Read footer to get firstTimestampNs (anchor)
    footer = json.loads(lines[-1])
    if footer.get("type") != "footer":
        raise ValueError(f"Expected footer at end of {sidecar_path}, got {footer.get('type')}")

    first_timestamp_ns = footer["firstTimestampNs"]

    # Read frame entries
    frames = []
    for line in lines[1:-1]:  # Skip header and footer
        data = json.loads(line)
        if data.get("type") != "frame":
            continue

        frame_index = data["frameIndex"]
        timestamp_realtime_ns = data["timestampNs"]
        exposure_ns = data.get("exposureNs", 0)

        frames.append((frame_index, timestamp_realtime_ns, exposure_ns))

    return first_timestamp_ns, frames, clock_offset_ns


def match_sidecar_to_video(
    sidecar_frames: List[Tuple[int, int, int]],
    video_pts: List[float],
    first_timestamp_ns: int,
    clock_offset_ns: int,
    match_threshold_ns: int = 5_000_000,  # 5ms default
) -> Tuple[Dict[int, Tuple[int, float]], List[Tuple[int, str]]]:
    """Match sidecar entries to video frames by timestamp.

    Args:
        sidecar_frames: [(frameIndex, timestampNs_realtime, exposureNs), ...]
        video_pts: [pts_seconds, ...] for each video frame
        first_timestamp_ns: Anchor timestamp (sidecar firstTimestampNs)
        clock_offset_ns: Clock offset to convert realtime to epoch
        match_threshold_ns: Max allowed error for a match (default 5ms)

    Returns:
        (matches, unmatched)
        matches: {frameIndex: (video_position, match_error_ns)}
        unmatched: [(frameIndex, reason), ...]
    """
    # Convert video PTS to absolute nanoseconds
    # video_t(k) = first_timestamp_ns + pts(k) * 1e9
    video_timestamps_ns = [first_timestamp_ns + int(pts * 1e9) for pts in video_pts]

    matches = {}
    unmatched = []

    for frame_idx, timestamp_realtime_ns, exposure_ns in sidecar_frames:
        # Sidecar timestamp is already in realtime, matches firstTimestampNs domain
        sidecar_t_ns = timestamp_realtime_ns

        # Find closest video frame
        best_video_pos = None
        best_error_ns = float('inf')

        for video_pos, video_t_ns in enumerate(video_timestamps_ns):
            error_ns = abs(sidecar_t_ns - video_t_ns)
            if error_ns < best_error_ns:
                best_error_ns = error_ns
                best_video_pos = video_pos

        # Check if match is within threshold
        if best_error_ns < match_threshold_ns:
            matches[frame_idx] = (best_video_pos, best_error_ns)
        else:
            reason = f"no_video_frame_within_{match_threshold_ns/1e6:.1f}ms (closest: {best_error_ns/1e6:.1f}ms)"
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
    """Extract frames using timestamp matching."""

    # Read video PTS
    video_pts = read_video_pts(video_path)

    # Parse sidecar
    first_timestamp_ns, sidecar_frames, clock_offset_ns = parse_sidecar(sidecar_path)

    # Filter sidecar to only needed frames
    sidecar_frames_needed = [(idx, t, e) for idx, t, e in sidecar_frames if idx in needed_frame_indices]

    # Match sidecar to video
    matches, unmatched = match_sidecar_to_video(
        sidecar_frames_needed,
        video_pts,
        first_timestamp_ns,
        clock_offset_ns,
    )

    stats = {
        "camera_id": camera_id,
        "video_frames": len(video_pts),
        "sidecar_frames": len(sidecar_frames),
        "requested": len(needed_frame_indices),
        "matched": len(matches),
        "unmatched": len(unmatched),
        "match_errors_ns": [err for _, err in matches.values()],
        "unmatched_details": unmatched,
    }

    if not matches:
        return stats

    # Extract matched frames
    with tempfile.TemporaryDirectory(prefix=f"extract_{camera_id[:8]}_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Decode entire video to temp (most efficient for high match ratio)
        width, height = target_resolution.split('x')

        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vf", f"scale={width}:{height}",
            "-q:v", str(100 - jpeg_quality),
            "-start_number", "0",
            str(tmp_path / "frame_%05d.jpg"),
            "-hide_banner",
            "-loglevel", "error",
        ]

        subprocess.run(cmd, check=True)

        # Copy matched frames to output
        for frame_idx, (video_pos, _) in matches.items():
            src_frame = tmp_path / f"frame_{video_pos:05d}.jpg"

            frame_dir = output_base / f"frame_{frame_idx:04d}"
            frame_dir.mkdir(parents=True, exist_ok=True)
            dst_frame = frame_dir / f"{camera_id}.jpg"

            if src_frame.exists():
                import shutil
                shutil.copy2(src_frame, dst_frame)
            else:
                print(f"  ⚠️  Missing decoded frame: {src_frame}")
                stats["matched"] -= 1

    return stats


def rebuild_model_for_available_images(
    model_dir: Path,
    available_image_ids: set,
    output_dir: Path,
):
    """Rebuild images.bin and times.txt to exclude entries without images.

    Args:
        model_dir: Original model directory
        available_image_ids: Set of image_ids that have extracted images
        output_dir: Output directory for rebuilt model
    """
    print(f"\n🔄 Rebuilding model to match available images...")

    # Read original images.bin
    images_bin = model_dir / "images.bin"
    new_images = []

    with open(images_bin, "rb") as f:
        num_images = struct.unpack("Q", f.read(8))[0]

        for _ in range(num_images):
            image_id = struct.unpack("I", f.read(4))[0]
            qvec = struct.unpack("dddd", f.read(32))
            tvec = struct.unpack("ddd", f.read(24))
            camera_id = struct.unpack("I", f.read(4))[0]

            # Read name
            name_bytes = b""
            while True:
                c = f.read(1)
                if c == b"\x00":
                    break
                name_bytes += c
            name = name_bytes.decode("utf-8")

            # Read points2D
            num_points2d = struct.unpack("Q", f.read(8))[0]
            points2d = []
            for _ in range(num_points2d):
                x, y = struct.unpack("dd", f.read(16))
                point3d_id = struct.unpack("Q", f.read(8))[0]
                points2d.append((x, y, point3d_id))

            # Keep only if image exists
            if image_id in available_image_ids:
                new_images.append({
                    "id": image_id,
                    "qvec": qvec,
                    "tvec": tvec,
                    "camera_id": camera_id,
                    "name": name,
                    "points2d": points2d,
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
                f.write(struct.pack("dd", x, y))
                f.write(struct.pack("Q", p3d_id))

    # Rebuild times.txt
    times_txt = model_dir / "times.txt"
    if times_txt.exists():
        times = {}
        with open(times_txt) as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.strip().split()
                if len(parts) >= 2:
                    img_id = int(parts[0])
                    t_ns = int(parts[1])
                    if img_id in available_image_ids:
                        times[img_id] = t_ns

        # Write new times.txt
        with open(output_dir / "times.txt", "w") as f:
            f.write("# colmap4d times: IMAGE_ID, T_NS (int64 ns)\n")
            f.write(f"# Number of images with a timestamp: {len(times)}\n")
            for img_id in sorted(times.keys()):
                f.write(f"{img_id} {times[img_id]}\n")

    # Copy other files
    for file in ["cameras.txt", "points3D.txt", "points_t.txt", "time_meta.json"]:
        src = model_dir / file
        if src.exists():
            import shutil
            shutil.copy2(src, output_dir / file)

    print(f"   Original images: {num_images}")
    print(f"   Rebuilt images: {len(new_images)}")
    print(f"   Excluded: {num_images - len(new_images)}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract frames from videos using timestamp matching (v2 - corrected)"
    )
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--shoot-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resolution", default="1920x1440")
    parser.add_argument("--jpeg-quality", type=int, default=85)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--match-threshold-ms", type=float, default=5.0,
                        help="Max timestamp error for frame matching (ms, default 5)")
    parser.add_argument("--rebuild-model", action="store_true",
                        help="Rebuild model files to exclude unmatched images")

    args = parser.parse_args()

    images_bin = args.model_dir / "images.bin"
    if not images_bin.exists():
        print(f"ERROR: {images_bin} not found")
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("="*80)
    print("Frame Extraction (Timestamp Matching v2)")
    print("="*80)
    print(f"Model: {args.model_dir}")
    print(f"Match threshold: {args.match_threshold_ms}ms")
    print()

    # Read model
    print("📖 Reading model...")
    images = read_images_bin(images_bin)
    print(f"   Found {len(images)} images")

    # Group by camera
    camera_frames = defaultdict(list)
    image_id_to_name = {}

    for img_id, name in images:
        frame_idx, camera_id = parse_image_name(name)
        camera_frames[camera_id].append(frame_idx)
        image_id_to_name[img_id] = name

    print(f"   Cameras: {len(camera_frames)}")

    # Extract frames
    print(f"\n🎬 Extracting with timestamp matching...")

    all_stats = []
    match_threshold_ns = int(args.match_threshold_ms * 1_000_000)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {}

        for camera_id, frame_indices in camera_frames.items():
            video_path = args.shoot_dir / camera_id / "video.mp4"
            sidecar_path = args.shoot_dir / camera_id / "timestamps.jsonl"

            if not video_path.exists() or not sidecar_path.exists():
                print(f"  ✗ {camera_id[:8]}: missing video or sidecar")
                continue

            print(f"  📹 {camera_id[:8]}: processing...")

            future = executor.submit(
                extract_camera_frames,
                camera_id,
                video_path,
                sidecar_path,
                frame_indices,
                args.output_dir,
                args.resolution,
                args.jpeg_quality,
            )
            futures[future] = camera_id

        for future in concurrent.futures.as_completed(futures):
            camera_id = futures[future]
            try:
                stats = future.result()
                all_stats.append(stats)

                matched = stats["matched"]
                unmatched = stats["unmatched"]
                errors_ns = stats["match_errors_ns"]

                if errors_ns:
                    mean_error_ms = sum(errors_ns) / len(errors_ns) / 1e6
                    max_error_ms = max(errors_ns) / 1e6
                    print(f"  ✓ {camera_id[:8]}: {matched} matched, {unmatched} unmatched")
                    print(f"      Match error: mean {mean_error_ms:.3f}ms, max {max_error_ms:.3f}ms")
                else:
                    print(f"  ⚠️  {camera_id[:8]}: 0 matches")

            except Exception as e:
                print(f"  ✗ {camera_id[:8]}: ERROR - {e}")
                import traceback
                traceback.print_exc()

    # Summary
    print(f"\n{'='*80}")
    print(f"📊 Extraction Summary")
    print(f"{'='*80}")

    total_requested = sum(s["requested"] for s in all_stats)
    total_matched = sum(s["matched"] for s in all_stats)
    total_unmatched = sum(s["unmatched"] for s in all_stats)

    print(f"Total requested: {total_requested}")
    print(f"Matched: {total_matched}")
    print(f"Unmatched: {total_unmatched}")

    # Match error distribution
    all_errors_ms = []
    for s in all_stats:
        all_errors_ms.extend([e / 1e6 for e in s["match_errors_ns"]])

    if all_errors_ms:
        all_errors_ms.sort()
        print(f"\nMatch error distribution (ms):")
        print(f"  Mean: {sum(all_errors_ms) / len(all_errors_ms):.3f}")
        print(f"  Median: {all_errors_ms[len(all_errors_ms)//2]:.3f}")
        print(f"  P95: {all_errors_ms[int(len(all_errors_ms)*0.95)]:.3f}")
        print(f"  Max: {max(all_errors_ms):.3f}")

    # Unmatched details
    print(f"\nUnmatched frames by camera:")
    for s in all_stats:
        if s["unmatched"] > 0:
            cam_id_short = s["camera_id"][:8]
            print(f"  {cam_id_short}: {s['unmatched']} frames")
            for frame_idx, reason in s["unmatched_details"][:3]:
                print(f"      frame_{frame_idx:04d}: {reason}")
            if len(s["unmatched_details"]) > 3:
                print(f"      ... and {len(s['unmatched_details']) - 3} more")

    # Disk usage
    total_size_mb = sum(f.stat().st_size for f in args.output_dir.rglob("*.jpg")) / (1024**2)
    print(f"\nOutput size: {total_size_mb:.1f} MB")
    if total_matched > 0:
        print(f"Average per frame: {total_size_mb / total_matched * 1024:.1f} KB")

    # Rebuild model if requested
    if args.rebuild_model:
        # Determine which image IDs have extracted files
        available_ids = set()
        for img_id, name in images:
            img_path = args.output_dir / name
            if img_path.exists():
                available_ids.add(img_id)

        rebuilt_dir = args.model_dir.parent / f"{args.model_dir.name}_rebuilt"
        rebuild_model_for_available_images(args.model_dir, available_ids, rebuilt_dir)

        print(f"\n✅ Rebuilt model saved to: {rebuilt_dir}")
        print(f"   Use this model for downstream tasks")

    elif total_unmatched > 0:
        print(f"\n⚠️  WARNING: {total_unmatched} images unmatched!")
        print(f"   Consider using --rebuild-model to create a consistent model")

    print(f"\nOutput directory: {args.output_dir}")


if __name__ == "__main__":
    main()
