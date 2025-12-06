#!/usr/bin/env python3
"""
FootyLevels Demo Script
Quick way to test the skill analysis pipeline.

Usage:
    # Test with webcam (records you performing the skill)
    python scripts/demo.py --webcam --skill cruyff_turn
    
    # Test with video files
    python scripts/demo.py --reference data/reference/cruyff.mp4 --user data/samples/my_attempt.mp4
    
    # Quick test with synthetic data
    python scripts/demo.py --test
"""

import argparse
import sys
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from footylevels import (
    PoseExtractor,
    PoseSequence,
    normalize_poses,
    SequenceAligner,
    PoseMetricsCalculator,
    SkillAnalyzer,
    generate_feedback
)
from footylevels.storage import AnalysisStorage
import numpy as np


def test_pose_extraction_webcam(duration: float = 5.0):
    """Test pose extraction using webcam."""
    print("=" * 50)
    print("Testing Pose Extraction with Webcam")
    print("=" * 50)
    
    extractor = PoseExtractor(model_complexity=1)
    
    try:
        print(f"\nRecording for {duration} seconds...")
        print("Stand in frame and perform some movements.")
        print("Press 'q' to stop early.\n")
        
        poses = extractor.extract_from_webcam(duration_seconds=duration)
        
        print(f"\n✅ Successfully extracted {len(poses)} frames")
        print(f"   FPS: {poses.fps:.1f}")
        print(f"   Resolution: {poses.width}x{poses.height}")
        print(f"   Duration: {poses.duration_ms/1000:.2f}s")
        
        if poses.frames:
            # Show some metrics
            metrics = PoseMetricsCalculator(poses)
            
            # Get knee angle at middle frame
            mid_frame = len(poses) // 2
            left_knee = metrics.get_knee_angle(mid_frame, 'left')
            right_knee = metrics.get_knee_angle(mid_frame, 'right')
            
            print(f"\n   Sample metrics at frame {mid_frame}:")
            print(f"   - Left knee angle: {left_knee:.1f}°")
            print(f"   - Right knee angle: {right_knee:.1f}°")
            
            # Hip rotation over time
            rotations = metrics.get_hip_rotation_over_time()
            print(f"   - Hip rotation range: {rotations.min():.1f}° to {rotations.max():.1f}°")
        
        return poses
        
    finally:
        extractor.close()


def test_pose_extraction_video(video_path: str):
    """Test pose extraction from video file."""
    print("=" * 50)
    print(f"Testing Pose Extraction from Video")
    print("=" * 50)
    
    video_path = Path(video_path)
    if not video_path.exists():
        print(f"❌ Video not found: {video_path}")
        return None
    
    extractor = PoseExtractor(model_complexity=2)
    
    try:
        print(f"\nProcessing: {video_path.name}")
        poses = extractor.extract_from_video(str(video_path))
        
        print(f"\n✅ Successfully extracted {len(poses)} frames")
        print(f"   FPS: {poses.fps:.1f}")
        print(f"   Duration: {poses.duration_ms/1000:.2f}s")
        
        return poses
        
    finally:
        extractor.close()


def test_alignment():
    """Test sequence alignment with synthetic data."""
    print("=" * 50)
    print("Testing Sequence Alignment")
    print("=" * 50)
    
    # Create synthetic pose sequences
    num_frames_ref = 60
    num_frames_user = 50
    
    print(f"\nCreating synthetic data...")
    print(f"  Reference: {num_frames_ref} frames")
    print(f"  User: {num_frames_user} frames")
    
    # Reference: smooth motion
    t_ref = np.linspace(0, 2*np.pi, num_frames_ref)
    ref_poses = np.zeros((num_frames_ref, 33, 4))
    for i in range(33):
        ref_poses[:, i, 0] = 0.5 + 0.1 * np.sin(t_ref + i * 0.1)
        ref_poses[:, i, 1] = 0.5 + 0.1 * np.cos(t_ref + i * 0.1)
        ref_poses[:, i, 3] = 1.0
    
    # User: similar but different timing and slight offset
    t_user = np.linspace(0.2, 2*np.pi + 0.2, num_frames_user)
    user_poses = np.zeros((num_frames_user, 33, 4))
    for i in range(33):
        user_poses[:, i, 0] = 0.5 + 0.1 * np.sin(t_user + i * 0.1) + 0.02  # slight offset
        user_poses[:, i, 1] = 0.5 + 0.1 * np.cos(t_user + i * 0.1) + 0.02
        user_poses[:, i, 3] = 1.0
    
    # Test alignment
    from footylevels.alignment import align_sequences, compute_aligned_deviations, get_deviation_summary
    
    result = align_sequences(ref_poses, user_poses)
    
    print(f"\n✅ Alignment completed")
    print(f"   Path length: {len(result.ref_indices)}")
    print(f"   DTW distance: {result.distance:.4f}")
    print(f"   Normalized distance: {result.normalized_distance:.4f}")
    
    # Test deviation computation
    deviations = compute_aligned_deviations(ref_poses, user_poses, result)
    summary = get_deviation_summary(deviations)
    
    print(f"\n   Joint 23 (left hip) deviation:")
    print(f"   - Mean: {summary[23]['mean']:.4f}")
    print(f"   - Max: {summary[23]['max']:.4f}")
    
    # Test similarity score
    aligner = SequenceAligner()
    similarity = aligner.get_similarity_score(result)
    print(f"\n   Similarity score: {similarity:.1f}%")
    
    return result


def test_metrics():
    """Test metric calculations."""
    print("=" * 50)
    print("Testing Metric Calculations")
    print("=" * 50)
    
    from footylevels.metrics import (
        calculate_angle_3points,
        calculate_distance,
        calculate_hip_rotation,
        calculate_velocity
    )
    
    # Test angle calculation
    p1 = np.array([0, 0, 0])
    p2 = np.array([1, 0, 0])
    p3 = np.array([1, 1, 0])
    angle = calculate_angle_3points(p1, p2, p3)
    print(f"\n✅ Angle calculation: {angle:.1f}° (expected: 90°)")
    
    # Test distance
    dist = calculate_distance(np.array([0, 0]), np.array([3, 4]))
    print(f"✅ Distance calculation: {dist:.1f} (expected: 5)")
    
    # Test hip rotation
    left_hip = np.array([0.4, 0.5])
    right_hip = np.array([0.6, 0.5])
    rotation = calculate_hip_rotation(left_hip, right_hip)
    print(f"✅ Hip rotation: {rotation:.1f}°")
    
    # Test velocity
    positions = np.array([[0, 0], [1, 0], [3, 0], [6, 0]])
    timestamps = np.array([0, 100, 200, 300])
    velocities = calculate_velocity(positions, timestamps)
    print(f"✅ Velocity: {velocities} (expected: [10, 20, 30])")


def run_full_analysis(ref_video: str, user_video: str, skill: str = 'cruyff_turn', comparative: bool = False):
    """Run full analysis pipeline."""
    print("=" * 50)
    print(f"Full Analysis: {skill}")
    print("=" * 50)
    
    print(f"\nReference: {ref_video}")
    print(f"User: {user_video}")
    if comparative:
        print("Mode: Comparative Scoring (relative to reference)")
    else:
        print("Mode: Absolute Scoring (relative to ideal values)")
    
    # Check if files exist
    if not Path(ref_video).exists():
        print(f"❌ Reference video not found: {ref_video}")
        print("   Please provide video files or use --test mode")
        return
    
    if not Path(user_video).exists():
        print(f"❌ User video not found: {user_video}")
        return
    
    # Run analysis
    analyzer = SkillAnalyzer()
    storage = AnalysisStorage()

    try:
        if comparative:
            result = analyzer.analyze_skill_comparative(skill, ref_video, user_video)
        else:
            result = analyzer.analyze_skill(skill, ref_video, user_video)

        # Generate feedback
        feedback = generate_feedback(result)

        print("\n" + "=" * 50)
        print(feedback.to_text())
        print("=" * 50)

        # Save analysis
        analysis_id = storage.save_analysis(
            result,
            feedback,
            name=None,  # Auto-generate name
            user_video_path=user_video,
            ref_video_path=ref_video
        )

        # Also print JSON for debugging
        print("\n📋 JSON Output:")
        print(json.dumps(result.to_dict(), indent=2))

        print(f"\n💾 Analysis saved with ID: {analysis_id}")
        print(f"   View saved results: ls data/results/json/")

    finally:
        analyzer.close()


def main():
    parser = argparse.ArgumentParser(description="FootyLevels Demo")
    
    parser.add_argument('--test', action='store_true',
                        help='Run tests with synthetic data')
    parser.add_argument('--webcam', action='store_true',
                        help='Test pose extraction with webcam')
    parser.add_argument('--duration', type=float, default=5.0,
                        help='Webcam recording duration in seconds')
    parser.add_argument('--reference', type=str,
                        help='Path to reference video')
    parser.add_argument('--user', type=str,
                        help='Path to user video')
    parser.add_argument('--skill', type=str, default='cruyff_turn',
                        choices=['cruyff_turn', 'step_over', 'elastico'],
                        help='Skill to analyze')
    parser.add_argument('--comparative', action='store_true',
                        help='Use comparative scoring (score relative to reference)')
    
    args = parser.parse_args()
    
    print("\n🎯 FootyLevels - AI Soccer Skill Analysis\n")
    
    if args.test:
        # Run all tests with synthetic data
        test_metrics()
        print()
        test_alignment()
        print("\n✅ All tests passed!")
        
    elif args.webcam:
        # Test with webcam
        test_pose_extraction_webcam(args.duration)
        
    elif args.reference and args.user:
        # Full analysis with video files
        run_full_analysis(args.reference, args.user, args.skill, comparative=args.comparative)
        
    else:
        # Default: run quick test
        print("Running quick test mode...\n")
        print("For full analysis, provide video files:")
        print("  python demo.py --reference ref.mp4 --user user.mp4\n")
        print("For webcam test:")
        print("  python demo.py --webcam\n")
        
        test_metrics()
        print()
        test_alignment()


if __name__ == "__main__":
    main()
