#!/usr/bin/env python3
"""
FootyLevels Demo Script
Run SAM 3D Body over a video and inspect the extracted 3D body sequence.

Usage:
    # Check the model is downloaded and see which device will be used
    python scripts/demo.py --check

    # Extract from a video file
    python scripts/demo.py --video data/reference/cruyff_turn.mp4

    # Extract from a skill's stored reference video, every 3rd frame
    python scripts/demo.py --skill cruyff_turn --stride 3

    # Extract and write an annotated overlay video
    python scripts/demo.py --video my_clip.mp4 --render out/annotated.mp4

    # List everything extracted so far
    python scripts/demo.py --list
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from footylevels import (
    BodyVisualizer,
    SAM3DBodyExtractor,
    SequenceStorage,
    SkillCatalog,
    resolve_device,
)

PROJECT_ROOT = Path(__file__).parent.parent
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"
SEQUENCE_DIR = PROJECT_ROOT / "data" / "sequences"


def check_model(extractor: SAM3DBodyExtractor) -> bool:
    """Report checkpoint status and the selected device."""
    print("=" * 60)
    print("SAM 3D Body status")
    print("=" * 60)
    print(f"  Checkpoint dir: {extractor.checkpoint_dir}")
    print(f"  Device:         {resolve_device()}")

    missing = extractor.check_checkpoint()
    if missing:
        print(f"\n  Missing files:  {', '.join(missing)}")
        print("\n  Download with:")
        print(
            f"    hf download facebook/sam-3d-body-dinov3 "
            f"--local-dir {extractor.checkpoint_dir}"
        )
        print("  (requires accepting the gated licence on Hugging Face first)")
        return False

    print("\n  Checkpoint complete.")
    return True


def resolve_video(args, catalog: SkillCatalog) -> Path:
    """Work out which video to process from --video or --skill."""
    if args.video:
        return Path(args.video)

    try:
        catalog.get_skill(args.skill)
    except KeyError:
        known = ", ".join(sorted(catalog.skills))
        raise SystemExit(f"Unknown skill: {args.skill}\nKnown skills: {known}")

    return REFERENCE_DIR / f"{args.skill}.mp4"


def extract(args) -> int:
    """Extract a body sequence from a video and save it."""
    catalog = SkillCatalog()
    video_path = resolve_video(args, catalog)

    if not video_path.exists():
        print(f"Video not found: {video_path}")
        return 1

    extractor = SAM3DBodyExtractor(
        device=args.device,
        include_vertices=args.vertices,
    )

    if not check_model(extractor):
        return 1

    print(f"\nExtracting: {video_path.name}")
    if args.stride > 1:
        print(f"  Processing every {args.stride} frames")
    print("  SAM 3D Body runs one forward pass per frame — this is slow.\n")

    try:
        sequence = extractor.extract_from_video(
            str(video_path),
            max_frames=args.max_frames,
            stride=args.stride,
        )
    finally:
        extractor.close()

    summary = sequence.summary()
    print("\nExtracted:")
    for key, value in summary.items():
        print(f"  {key:22s} {value}")

    if not sequence.primary_detections:
        print("\nNo people detected — nothing to save.")
        return 1

    storage = SequenceStorage(str(SEQUENCE_DIR))
    sequence_id = storage.save_sequence(
        sequence, name=video_path.stem, video_path=str(video_path)
    )
    print(f"\nSaved sequence: {sequence_id}")
    print(f"  {SEQUENCE_DIR / (sequence_id + '.npz')}")

    if args.render:
        print(f"\nRendering overlay to {args.render} ...")
        BodyVisualizer().render_video(
            str(video_path), sequence, args.render, label_indices=args.label_joints
        )
        print("  Done.")

    return 0


def list_sequences() -> int:
    """Print every stored sequence."""
    sequences = SequenceStorage(str(SEQUENCE_DIR)).list_sequences()

    if not sequences:
        print("No sequences stored yet.")
        return 0

    print(f"{'id':40s} {'frames':>8s} {'joints':>7s}  source")
    print("-" * 80)
    for meta in sequences:
        detected = f"{meta.get('frames_with_detection', 0)}/{meta.get('total_frames', 0)}"
        print(
            f"{meta['id']:40s} {detected:>8s} "
            f"{str(meta.get('num_joints', '-')):>7s}  "
            f"{meta.get('source_video') or '-'}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FootyLevels 3D body extraction demo")

    source = parser.add_mutually_exclusive_group()
    source.add_argument("--video", type=str, help="Path to a video file")
    source.add_argument("--skill", type=str, help="Use a skill's reference video")

    parser.add_argument("--check", action="store_true",
                        help="Report checkpoint status and exit")
    parser.add_argument("--list", action="store_true",
                        help="List stored sequences and exit")
    parser.add_argument("--stride", type=int, default=1,
                        help="Process every Nth frame (default: 1)")
    parser.add_argument("--max-frames", type=int, default=None,
                        help="Stop after this many processed frames")
    parser.add_argument("--vertices", action="store_true",
                        help="Keep full meshes (~220 KB per frame)")
    parser.add_argument("--device", type=str, default=None,
                        choices=["cuda", "mps", "cpu"],
                        help="Force a device (default: auto-detect)")
    parser.add_argument("--render", type=str, default=None,
                        help="Write an annotated overlay video here")
    parser.add_argument("--label-joints", action="store_true",
                        help="Label joint indices in the rendered overlay")

    args = parser.parse_args()

    print("\nFootyLevels - 3D body extraction\n")

    if args.list:
        return list_sequences()

    if args.check:
        return 0 if check_model(SAM3DBodyExtractor(device=args.device)) else 1

    if not args.video and not args.skill:
        parser.print_help()
        return 0

    return extract(args)


if __name__ == "__main__":
    sys.exit(main())
