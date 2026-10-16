#!/usr/bin/env python3
"""
FootyLevels - single image to 3D body model.

Reads photos from data/reference/pictures_for_model/, runs them through
SAM 3D Body, and writes each detected person out as a 3D mesh file.

Usage:
    # Process every picture in the input folder
    python main.py

    # Process one picture
    python main.py --image player.jpg

    # Write PLY instead of OBJ, and save a 2D keypoint preview
    python main.py --format ply --preview

    # If the mesh loads upside down in your viewer
    python main.py --y-up
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import cv2

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from footylevels import BodyVisualizer, SAM3DBodyExtractor, resolve_device
from footylevels.mesh import write_mesh

PROJECT_ROOT = Path(__file__).parent
INPUT_DIR = PROJECT_ROOT / "data" / "reference" / "pictures_for_model"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output" / "meshes"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def find_images(image_name: Optional[str] = None) -> List[Path]:
    """Collect the images to process, newest-name-first for stable ordering."""
    if not INPUT_DIR.exists():
        raise SystemExit(
            f"Input folder does not exist: {INPUT_DIR}\n"
            f"Create it and drop some photos in:\n"
            f"  mkdir -p {INPUT_DIR}"
        )

    if image_name:
        path = INPUT_DIR / image_name
        if not path.exists():
            raise SystemExit(f"Image not found: {path}")
        return [path]

    images = sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise SystemExit(
            f"No images found in {INPUT_DIR}\n"
            f"Supported types: {', '.join(sorted(IMAGE_SUFFIXES))}"
        )
    return images


def require_checkpoint(extractor: SAM3DBodyExtractor) -> None:
    """Stop early with instructions if the model weights are not downloaded."""
    missing = extractor.check_checkpoint()
    if not missing:
        return

    raise SystemExit(
        f"SAM 3D Body checkpoint incomplete in {extractor.checkpoint_dir}\n"
        f"  Missing: {', '.join(missing)}\n\n"
        f"Download it (~2.8 GB, gated - accept the licence on Hugging Face first):\n"
        f"  hf download facebook/sam-3d-body-dinov3 "
        f"--local-dir {extractor.checkpoint_dir}"
    )


def process_image(
    extractor: SAM3DBodyExtractor,
    image_path: Path,
    output_dir: Path,
    mesh_format: str,
    y_up: bool,
    preview: bool,
) -> int:
    """
    Reconstruct every person in one image and write their meshes.

    Returns:
        How many meshes were written.
    """
    print(f"\n{image_path.name}")

    image_bgr = cv2.imread(str(image_path))
    if image_bgr is None:
        print("  Could not read this file — skipping.")
        return 0

    height, width = image_bgr.shape[:2]
    print(f"  {width}x{height}, running SAM 3D Body ...")

    frame = extractor.extract_from_image(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))

    if not frame.detections:
        print("  No person detected.")
        return 0

    faces = extractor.faces
    if faces is None:
        print("  Model exposed no face topology — writing point clouds instead.")

    written = 0
    for i, detection in enumerate(frame.detections):
        if detection.vertices is None:
            print(f"  Person {i + 1}: model returned no mesh vertices — skipping.")
            continue

        suffix = "" if len(frame.detections) == 1 else f"_person{i + 1}"
        output_path = output_dir / f"{image_path.stem}{suffix}.{mesh_format}"

        write_mesh(
            detection.vertices,
            faces,
            str(output_path),
            y_up=y_up,
            comment=f"source: {image_path.name}",
        )

        size_kb = output_path.stat().st_size / 1024
        print(
            f"  Person {i + 1}: {len(detection.vertices):,} vertices, "
            f"{detection.num_joints} joints -> {output_path.name} ({size_kb:.0f} KB)"
        )
        written += 1

    if preview:
        annotated = image_bgr.copy()
        visualizer = BodyVisualizer()
        for detection in frame.detections:
            visualizer.draw_keypoints(annotated, detection)
            visualizer.draw_bbox(annotated, detection)

        preview_path = output_dir / f"{image_path.stem}_keypoints.jpg"
        cv2.imwrite(str(preview_path), annotated)
        print(f"  Preview -> {preview_path.name}")

    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Turn a photo into a 3D body model using SAM 3D Body"
    )
    parser.add_argument("--image", type=str, default=None,
                        help="Process one image by filename (default: all of them)")
    parser.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR),
                        help=f"Where to write meshes (default: {OUTPUT_DIR})")
    parser.add_argument("--format", type=str, default="obj", choices=["obj", "ply"],
                        help="Mesh file format (default: obj)")
    parser.add_argument("--y-up", action="store_true",
                        help="Flip to Y-up if the mesh loads upside down")
    parser.add_argument("--preview", action="store_true",
                        help="Also save a 2D keypoint overlay image")
    parser.add_argument("--device", type=str, default=None,
                        choices=["cuda", "mps", "cpu"],
                        help="Force a device (default: auto-detect)")

    args = parser.parse_args()

    print("\nFootyLevels - image to 3D body model")

    images = find_images(args.image)

    extractor = SAM3DBodyExtractor(device=args.device, include_vertices=True)
    require_checkpoint(extractor)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Device: {resolve_device(args.device)}")
    print(f"  Input:  {INPUT_DIR}")
    print(f"  Output: {output_dir}")
    print(f"  Found {len(images)} image(s)")

    total = 0
    try:
        for image_path in images:
            total += process_image(
                extractor, image_path, output_dir,
                args.format, args.y_up, args.preview,
            )
    finally:
        extractor.close()

    print(f"\nWrote {total} mesh file(s) to {output_dir}")
    if total:
        print("Open one with: Preview, Blender, or https://3dviewer.net")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
