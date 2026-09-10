#!/usr/bin/env python3
"""DEPRECATED: This script has a critical bug and should not be used.

BUG: Assumes "Nth sidecar entry = Nth video frame", which breaks when encoder drops
frames. This causes duplicate images and incorrect timestamp associations.

USE INSTEAD: extract_frames.py (timestamp matching with offset correction)

See git commit b0e472f for details on the bug and fix.

---

Original description:
Extract frames from videos according to colmap4d model image names.

Reads a colmap4d model's images.bin, extracts the corresponding frames from source
videos using timestamp sidecars to map frameIndex → video frame position, and writes
them to the paths specified in the model.

This tool is designed for fixed-rig multi-camera captures where:
- Videos are stored as <shoot_dir>/<camera_id>/video.mp4
- Timestamp sidecars are at <shoot_dir>/<camera_id>/timestamps.jsonl
- Model image names are like "frame_0007/<camera_id>.jpg"

Usage:
    python extract_frames_from_videos.py \
        --model-dir /path/to/colmap4d_output \
        --shoot-dir /path/to/shoot_dir \
        --output-dir /path/to/output/images \
        --resolution 1920x1440 \
        --jpeg-quality 85

The script:
1. Reads images.bin to get required image names
2. Parses sidecars to build frameIndex → video_frame_position maps
3. Extracts frames in batches per camera (parallel ffmpeg)
4. Downsamples and places frames at paths matching model NAMEs
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
from typing import Dict, List, Tuple

import concurrent.futures


def read_images_bin(images_bin_path: Path) -> List[str]:
    """Read image NAMEs from images.bin.

    Returns:
        List of image names (e.g., "frame_0007/12aebeb5-de8f-4b67-b8ec-9fd3246f7330.jpg")
    """
    image_names = []

    with open(images_bin_path, "rb") as f:
        num_images = struct.unpack("Q", f.read(8))[0]

        for _ in range(num_images):
            # Skip: image_id (4), qvec (32), tvec (24), camera_id (4)
            f.read(4 + 32 + 24 + 4)

            # Read image name (null-terminated string)
            name_bytes = b""
            while True:
                c = f.read(1)
                if c == b"\x00":
                    break
                name_bytes += c
            image_name = name_bytes.decode("utf-8")
            image_names.append(image_name)

            # Skip num_points2D (8) and point2D data (24 bytes each)
            num_points2d = struct.unpack("Q", f.read(8))[0]
            f.read(num_points2d * 24)

    return image_names


def parse_image_name(name: str) -> Tuple[int, str]:
    """Parse image name to extract frame_index and camera_id.

    Args:
        name: Image name like "frame_0007/12aebeb5-de8f-4b67-b8ec-9fd3246f7330.jpg"

    Returns:
        (frame_index, camera_id)
    """
    parts = name.split("/")
    frame_part = parts[0]  # "frame_0007"
    camera_file = parts[1]  # "12aebeb5-de8f-4b67-b8ec-9fd3246f7330.jpg"

    frame_index = int(frame_part.replace("frame_", ""))
    camera_id = camera_file.replace(".jpg", "")

    return frame_index, camera_id


def build_frame_index_map(sidecar_path: Path) -> Dict[int, int]:
    """Build mapping from frameIndex to video frame position.

    The sidecar contains frame entries in the order they appear in the video.
    The Nth frame entry (0-indexed) corresponds to the Nth frame in the video.

    Args:
        sidecar_path: Path to timestamps.jsonl

    Returns:
        Dict mapping frameIndex → video_frame_position (0-indexed)
    """
    frame_index_to_position = {}

    lines = sidecar_path.read_text().strip().split('\n')

    video_position = 0
    for line in lines[1:]:  # Skip header
        data = json.loads(line)
        if data.get("type") != "frame":
            continue

        frame_index = data["frameIndex"]
        frame_index_to_position[frame_index] = video_position
        video_position += 1

    return frame_index_to_position


def extract_camera_frames(
    camera_id: str,
    video_path: Path,
    sidecar_path: Path,
    needed_frame_indices: List[int],
    output_base: Path,
    target_resolution: str,
    jpeg_quality: int,
) -> Dict[str, any]:
    """Extract needed frames for one camera.

    Args:
        camera_id: Camera identifier (UUID)
        video_path: Path to video.mp4
        sidecar_path: Path to timestamps.jsonl
        needed_frame_indices: List of frameIndex values to extract
        output_base: Base output directory
        target_resolution: Target resolution like "1920x1440"
        jpeg_quality: JPEG quality (1-100)

    Returns:
        Stats dict with extracted count, skipped, errors
    """
    # Build frameIndex → video_position map
    frame_map = build_frame_index_map(sidecar_path)

    # Create temp directory for intermediate frames
    with tempfile.TemporaryDirectory(prefix=f"extract_{camera_id[:8]}_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Determine which video positions to extract
        positions_to_extract = []
        for frame_idx in needed_frame_indices:
            if frame_idx in frame_map:
                positions_to_extract.append((frame_idx, frame_map[frame_idx]))
            else:
                print(f"  ⚠️  Camera {camera_id[:8]}: frameIndex {frame_idx} not in sidecar")

        if not positions_to_extract:
            return {"extracted": 0, "skipped": len(needed_frame_indices), "errors": 0}

        # Extract all needed frames in one ffmpeg call
        # Build select filter: select frames at specific positions
        positions = [pos for _, pos in positions_to_extract]

        # For efficiency: if extracting most frames, decode all and filter;
        # otherwise use select filter
        total_frames = len(frame_map)
        extract_ratio = len(positions) / total_frames if total_frames > 0 else 0

        if extract_ratio > 0.8:
            # Extract all frames (more efficient for high ratio)
            extract_all_and_filter(
                video_path, tmp_path, positions_to_extract,
                output_base, camera_id, target_resolution, jpeg_quality
            )
        else:
            # Use select filter for sparse extraction
            extract_with_select_filter(
                video_path, tmp_path, positions_to_extract,
                output_base, camera_id, target_resolution, jpeg_quality
            )

        stats = {
            "extracted": len(positions_to_extract),
            "skipped": len(needed_frame_indices) - len(positions_to_extract),
            "errors": 0,
        }

        return stats


def extract_all_and_filter(
    video_path: Path,
    tmp_path: Path,
    positions_to_extract: List[Tuple[int, int]],
    output_base: Path,
    camera_id: str,
    target_resolution: str,
    jpeg_quality: int,
):
    """Extract all frames, then copy needed ones to output."""
    # Decode entire video to temp directory
    width, height = target_resolution.split('x')

    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", f"scale={width}:{height}",
        "-q:v", str(100 - jpeg_quality),  # ffmpeg uses inverted scale for -q:v
        "-start_number", "0",
        str(tmp_path / "frame_%05d.jpg"),
        "-hide_banner",
        "-loglevel", "error",
    ]

    subprocess.run(cmd, check=True)

    # Copy frames to final locations
    for frame_idx, video_pos in positions_to_extract:
        src_frame = tmp_path / f"frame_{video_pos:05d}.jpg"

        # Output path: <output_base>/frame_<frameIdx>/<camera_id>.jpg
        frame_dir = output_base / f"frame_{frame_idx:04d}"
        frame_dir.mkdir(parents=True, exist_ok=True)
        dst_frame = frame_dir / f"{camera_id}.jpg"

        if src_frame.exists():
            # Copy frame
            import shutil
            shutil.copy2(src_frame, dst_frame)
        else:
            print(f"  ⚠️  Missing intermediate frame: {src_frame}")


def extract_with_select_filter(
    video_path: Path,
    tmp_path: Path,
    positions_to_extract: List[Tuple[int, int]],
    output_base: Path,
    camera_id: str,
    target_resolution: str,
    jpeg_quality: int,
):
    """Extract only needed frames using select filter."""
    # Build select expression
    positions = sorted(set(pos for _, pos in positions_to_extract))
    select_expr = "+".join(f"eq(n\\,{pos})" for pos in positions)

    width, height = target_resolution.split('x')

    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", f"select='{select_expr}',scale={width}:{height}",
        "-vsync", "0",  # Don't duplicate/drop frames
        "-q:v", str(100 - jpeg_quality),
        "-start_number", "0",
        str(tmp_path / "selected_%05d.jpg"),
        "-hide_banner",
        "-loglevel", "error",
    ]

    subprocess.run(cmd, check=True)

    # Rename frames to final locations
    # The output frames are numbered 0, 1, 2, ... in the order they were selected
    position_to_index = {pos: idx for idx, (_, pos) in enumerate(sorted(positions_to_extract, key=lambda x: x[1]))}

    for frame_idx, video_pos in positions_to_extract:
        selected_idx = position_to_index[video_pos]
        src_frame = tmp_path / f"selected_{selected_idx:05d}.jpg"

        frame_dir = output_base / f"frame_{frame_idx:04d}"
        frame_dir.mkdir(parents=True, exist_ok=True)
        dst_frame = frame_dir / f"{camera_id}.jpg"

        if src_frame.exists():
            import shutil
            shutil.copy2(src_frame, dst_frame)
        else:
            print(f"  ⚠️  Missing selected frame: {src_frame}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract frames from videos according to colmap4d model"
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        required=True,
        help="colmap4d model directory (contains images.bin)",
    )
    parser.add_argument(
        "--shoot-dir",
        type=Path,
        required=True,
        help="Shoot directory containing <camera_id>/video.mp4 and timestamps.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for extracted frames",
    )
    parser.add_argument(
        "--resolution",
        default="1920x1440",
        help="Target resolution (default: 1920x1440 for 4:3 aspect ratio)",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=85,
        help="JPEG quality 1-100 (default: 85)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Max parallel camera extractions (default: 4)",
    )

    args = parser.parse_args()

    # Validate paths
    images_bin = args.model_dir / "images.bin"
    if not images_bin.exists():
        print(f"ERROR: {images_bin} not found")
        sys.exit(1)

    if not args.shoot_dir.exists():
        print(f"ERROR: {args.shoot_dir} not found")
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("="*80)
    print("Frame Extraction from Videos")
    print("="*80)
    print(f"Model: {args.model_dir}")
    print(f"Shoot: {args.shoot_dir}")
    print(f"Output: {args.output_dir}")
    print(f"Resolution: {args.resolution}")
    print(f"JPEG quality: {args.jpeg_quality}")
    print()

    # Read model to get required image names
    print("📖 Reading model...")
    image_names = read_images_bin(images_bin)
    print(f"   Found {len(image_names)} images in model")

    # Group by camera and collect needed frameIndices
    camera_frames = defaultdict(list)
    for name in image_names:
        frame_idx, camera_id = parse_image_name(name)
        camera_frames[camera_id].append(frame_idx)

    print(f"   Cameras: {len(camera_frames)}")
    for camera_id in sorted(camera_frames.keys()):
        print(f"      {camera_id[:8]}: {len(camera_frames[camera_id])} frames")

    # Extract frames per camera (in parallel)
    print(f"\n🎬 Extracting frames (max {args.max_workers} cameras in parallel)...")

    total_extracted = 0
    total_skipped = 0
    total_errors = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {}

        for camera_id, frame_indices in camera_frames.items():
            video_path = args.shoot_dir / camera_id / "video.mp4"
            sidecar_path = args.shoot_dir / camera_id / "timestamps.jsonl"

            if not video_path.exists():
                print(f"  ⚠️  Video not found: {video_path}")
                total_skipped += len(frame_indices)
                continue

            if not sidecar_path.exists():
                print(f"  ⚠️  Sidecar not found: {sidecar_path}")
                total_skipped += len(frame_indices)
                continue

            print(f"  📹 {camera_id[:8]}: extracting {len(frame_indices)} frames...")

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

        # Wait for all extractions to complete
        for future in concurrent.futures.as_completed(futures):
            camera_id = futures[future]
            try:
                stats = future.result()
                total_extracted += stats["extracted"]
                total_skipped += stats["skipped"]
                total_errors += stats["errors"]
                print(f"  ✓ {camera_id[:8]}: {stats['extracted']} frames extracted")
            except Exception as e:
                print(f"  ✗ {camera_id[:8]}: ERROR - {e}")
                total_errors += len(camera_frames[camera_id])

    # Summary
    print(f"\n{'='*80}")
    print(f"📊 Extraction Summary")
    print(f"{'='*80}")
    print(f"Total frames: {len(image_names)}")
    print(f"Extracted: {total_extracted}")
    print(f"Skipped: {total_skipped}")
    print(f"Errors: {total_errors}")

    # Compute output size
    total_size_mb = sum(f.stat().st_size for f in args.output_dir.rglob("*.jpg")) / (1024**2)
    print(f"Output size: {total_size_mb:.1f} MB")
    print(f"Average per frame: {total_size_mb / total_extracted:.2f} MB" if total_extracted > 0 else "N/A")

    if total_extracted == len(image_names):
        print(f"\n✅ All frames extracted successfully!")
    else:
        print(f"\n⚠️  Warning: {len(image_names) - total_extracted} frames missing!")

    print(f"\nOutput directory: {args.output_dir}")


if __name__ == "__main__":
    main()
