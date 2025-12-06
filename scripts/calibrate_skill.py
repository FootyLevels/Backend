#!/usr/bin/env python3
"""
Calibrate Skill Configuration
Analyze a reference video and suggest ideal checkpoint values.

Usage:
    python scripts/calibrate_skill.py --skill cruyff_turn --video data/reference/cruyff_turn.mp4
"""

import argparse
import sys
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from footylevels import PoseExtractor, normalize_poses, PoseMetricsCalculator, SkillAnalyzer
import numpy as np


def calibrate_skill(skill_name: str, reference_video: str):
    """
    Analyze reference video and suggest calibrated checkpoint values.

    Args:
        skill_name: Name of the skill
        reference_video: Path to reference video
    """
    print("=" * 60)
    print(f"Calibrating Skill: {skill_name}")
    print("=" * 60)
    print(f"\nAnalyzing reference video: {reference_video}\n")

    # Extract poses
    extractor = PoseExtractor(model_complexity=2)

    try:
        print("Extracting poses...")
        poses = extractor.extract_from_video(reference_video)
        poses = normalize_poses(poses)

        print(f"✓ Extracted {len(poses)} frames\n")

        # Calculate metrics
        metrics = PoseMetricsCalculator(poses)

        # Analyze different checkpoints
        print("Measured Metrics from Reference Video:")
        print("-" * 60)

        # 1. Hip rotation
        rotations = metrics.get_hip_rotation_over_time()
        max_rotation = np.max(np.abs(rotations))
        avg_rotation = np.mean(np.abs(rotations))
        print(f"\n📐 Hip Rotation:")
        print(f"   Max: {max_rotation:.1f}°")
        print(f"   Average: {avg_rotation:.1f}°")
        print(f"   Suggested ideal: {max_rotation * 0.95:.1f}°")
        print(f"   Suggested threshold_good: {max_rotation * 0.85:.1f}°")
        print(f"   Suggested threshold_acceptable: {max_rotation * 0.70:.1f}°")

        # 2. Knee angles
        mid_frame = len(poses) // 2
        left_knee = metrics.get_knee_angle(mid_frame, 'left')
        right_knee = metrics.get_knee_angle(mid_frame, 'right')

        # Sample knee angles throughout
        knee_angles = []
        for i in range(0, len(poses), 5):
            knee_angles.append(metrics.get_knee_angle(i, 'left'))

        min_knee = np.min(knee_angles)
        avg_knee = np.mean(knee_angles)

        print(f"\n🦵 Knee Flexion (at mid-frame):")
        print(f"   Left: {left_knee:.1f}°")
        print(f"   Right: {right_knee:.1f}°")
        print(f"   Min knee angle: {min_knee:.1f}°")
        print(f"   Average: {avg_knee:.1f}°")
        print(f"   Suggested ideal: {min_knee:.1f}°")
        print(f"   Suggested threshold_good: {min_knee * 1.1:.1f}°")

        # 3. Plant foot distance
        plant_distances = []
        for i in range(len(poses)):
            plant_distances.append(metrics.get_plant_foot_distance(i, 'left'))

        min_plant_dist = np.min(plant_distances)
        avg_plant_dist = np.mean(plant_distances)

        print(f"\n👟 Plant Foot Position:")
        print(f"   Min distance: {min_plant_dist:.3f}")
        print(f"   Average: {avg_plant_dist:.3f}")
        print(f"   Suggested ideal: {min_plant_dist:.3f}")
        print(f"   Suggested threshold_good: {min_plant_dist * 1.5:.3f}")
        print(f"   Suggested threshold_acceptable: {min_plant_dist * 2.0:.3f}")

        # 4. Shoulder direction change
        shoulder_change, frame_idx = metrics.get_shoulder_direction_change()

        print(f"\n💪 Shoulder Fake:")
        print(f"   Max direction change: {shoulder_change:.1f}°")
        print(f"   Occurred at frame: {frame_idx}")
        print(f"   Suggested ideal: {shoulder_change:.1f}°")
        print(f"   Suggested threshold_good: {shoulder_change * 0.8:.1f}°")

        # 5. Exit velocity
        velocities = metrics.get_center_of_mass_velocity()
        if len(velocities) > 10:
            early_vel = np.mean(velocities[:len(velocities)//3])
            late_vel = np.mean(velocities[-len(velocities)//3:])
            vel_change = late_vel / (early_vel + 0.01)

            print(f"\n🚀 Exit Acceleration:")
            print(f"   Velocity change: {vel_change:.2f}x")
            print(f"   Suggested ideal: {vel_change:.2f}")
            print(f"   Suggested threshold_good: {vel_change * 0.8:.2f}")

        # Generate suggested YAML config
        print("\n" + "=" * 60)
        print("Suggested config/skills.yaml updates:")
        print("=" * 60)

        suggested_config = f"""
  {skill_name}:
    checkpoints:
      - id: plant_foot_position
        ideal_value: {min_plant_dist:.3f}
        threshold_good: {min_plant_dist * 1.5:.3f}
        threshold_acceptable: {min_plant_dist * 2.0:.3f}

      - id: hip_rotation
        ideal_value: {max_rotation * 0.95:.1f}
        threshold_good: {max_rotation * 0.85:.1f}
        threshold_acceptable: {max_rotation * 0.70:.1f}

      - id: shoulder_deception
        ideal_value: {shoulder_change:.1f}
        threshold_good: {shoulder_change * 0.8:.1f}
        threshold_acceptable: {shoulder_change * 0.6:.1f}

      - id: knee_bend
        ideal_value: {min_knee:.1f}
        threshold_good: {min_knee * 1.15:.1f}
        threshold_acceptable: {min_knee * 1.30:.1f}
"""

        print(suggested_config)

    finally:
        extractor.close()


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate skill configuration based on reference video"
    )

    parser.add_argument(
        '--skill',
        type=str,
        required=True,
        help='Skill name (e.g., cruyff_turn)'
    )

    parser.add_argument(
        '--video',
        type=str,
        required=True,
        help='Path to reference video'
    )

    args = parser.parse_args()

    if not Path(args.video).exists():
        print(f"Error: Video not found: {args.video}")
        return 1

    calibrate_skill(args.skill, args.video)

    return 0


if __name__ == "__main__":
    sys.exit(main())
