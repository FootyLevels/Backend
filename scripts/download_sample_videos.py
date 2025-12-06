#!/usr/bin/env python3
"""
Download Sample Videos Script
Downloads sample reference videos for testing FootyLevels.

Usage:
    python scripts/download_sample_videos.py
    python scripts/download_sample_videos.py --skill cruyff_turn
"""

import argparse
import urllib.request
from pathlib import Path
import sys

# Sample video URLs (these are placeholders - replace with actual URLs)
# In a real implementation, you'd host reference videos on a CDN or cloud storage
SAMPLE_VIDEOS = {
    'cruyff_turn': {
        'url': 'https://example.com/cruyff_turn_reference.mp4',
        'description': 'Professional Cruyff turn demonstration',
        'difficulty': 'intermediate'
    },
    'step_over': {
        'url': 'https://example.com/step_over_reference.mp4',
        'description': 'Step over skill demonstration',
        'difficulty': 'beginner'
    },
    'elastico': {
        'url': 'https://example.com/elastico_reference.mp4',
        'description': 'Elastico (flip flap) demonstration',
        'difficulty': 'advanced'
    }
}


def download_file(url: str, dest_path: Path, show_progress: bool = True):
    """
    Download a file from URL to destination.

    Args:
        url: URL to download from
        dest_path: Where to save the file
        show_progress: Whether to show download progress
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    def progress_hook(count, block_size, total_size):
        if show_progress and total_size > 0:
            percent = int(count * block_size * 100 / total_size)
            sys.stdout.write(f"\r  Downloading: {percent}%")
            sys.stdout.flush()

    try:
        print(f"Downloading to {dest_path}...")
        urllib.request.urlretrieve(url, str(dest_path), progress_hook)
        if show_progress:
            print("\n  ✓ Complete")
        return True
    except Exception as e:
        print(f"\n  ✗ Failed: {e}")
        return False


def create_placeholder_video(dest_path: Path):
    """
    Create a placeholder video file when download isn't available.

    This creates a simple black video using OpenCV.
    """
    try:
        import cv2
        import numpy as np

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Create a simple 5-second black video
        fps = 30
        duration = 5
        width, height = 640, 480

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(str(dest_path), fourcc, fps, (width, height))

        # Write frames
        for i in range(fps * duration):
            frame = np.zeros((height, width, 3), dtype=np.uint8)

            # Add text
            text = "Placeholder Video"
            cv2.putText(frame, text, (width//4, height//2),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

            out.write(frame)

        out.release()
        print(f"  Created placeholder video at {dest_path}")
        return True

    except ImportError:
        print("  OpenCV not available, cannot create placeholder")
        return False


def download_all_samples(output_dir: Path, use_placeholders: bool = False):
    """
    Download all sample reference videos.

    Args:
        output_dir: Directory to save videos
        use_placeholders: If True, create placeholder videos instead of downloading
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("FootyLevels - Sample Video Downloader")
    print("=" * 60)

    for skill_id, info in SAMPLE_VIDEOS.items():
        print(f"\n{skill_id}:")
        print(f"  Description: {info['description']}")
        print(f"  Difficulty: {info['difficulty']}")

        dest_path = output_dir / f"{skill_id}.mp4"

        if dest_path.exists():
            print(f"  ✓ Already exists at {dest_path}")
            continue

        if use_placeholders:
            create_placeholder_video(dest_path)
        else:
            success = download_file(info['url'], dest_path)

            if not success:
                print("  Creating placeholder instead...")
                create_placeholder_video(dest_path)

    print("\n" + "=" * 60)
    print("Download complete!")
    print(f"Videos saved to: {output_dir}")
    print("=" * 60)


def download_single_skill(skill: str, output_dir: Path, use_placeholder: bool = False):
    """Download a single skill's reference video."""
    if skill not in SAMPLE_VIDEOS:
        print(f"Error: Unknown skill '{skill}'")
        print(f"Available skills: {', '.join(SAMPLE_VIDEOS.keys())}")
        return False

    info = SAMPLE_VIDEOS[skill]
    dest_path = output_dir / f"{skill}.mp4"

    print(f"Downloading {skill}...")
    print(f"  Description: {info['description']}")

    if dest_path.exists():
        print(f"  ✓ Already exists at {dest_path}")
        return True

    if use_placeholder:
        return create_placeholder_video(dest_path)
    else:
        success = download_file(info['url'], dest_path)
        if not success:
            print("  Creating placeholder instead...")
            return create_placeholder_video(dest_path)
        return success


def list_available_skills():
    """List all available sample videos."""
    print("\nAvailable Skills:")
    print("-" * 60)

    for skill_id, info in SAMPLE_VIDEOS.items():
        print(f"\n{skill_id}")
        print(f"  Description: {info['description']}")
        print(f"  Difficulty: {info['difficulty']}")
        print(f"  URL: {info['url']}")


def main():
    parser = argparse.ArgumentParser(
        description="Download sample reference videos for FootyLevels"
    )

    parser.add_argument(
        '--skill',
        type=str,
        help='Download specific skill (e.g., cruyff_turn)'
    )

    parser.add_argument(
        '--output',
        type=str,
        default='data/reference',
        help='Output directory (default: data/reference)'
    )

    parser.add_argument(
        '--list',
        action='store_true',
        help='List available skills'
    )

    parser.add_argument(
        '--placeholder',
        action='store_true',
        help='Create placeholder videos instead of downloading'
    )

    args = parser.parse_args()

    # Get project root directory
    project_root = Path(__file__).parent.parent
    output_dir = project_root / args.output

    if args.list:
        list_available_skills()
        return

    if args.skill:
        download_single_skill(args.skill, output_dir, args.placeholder)
    else:
        download_all_samples(output_dir, args.placeholder)

    print("\nNote: These are sample/placeholder videos.")
    print("For production, replace with actual professional skill demonstrations.")


if __name__ == "__main__":
    main()
