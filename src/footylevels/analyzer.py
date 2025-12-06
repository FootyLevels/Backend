"""
Analyzer Module
Main orchestration for analyzing soccer skills.
Combines pose extraction, alignment, metrics, and feedback generation.
"""

import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import numpy as np

from .pose_extractor import PoseExtractor, PoseSequence, normalize_poses, LandmarkIndex
from .alignment import SequenceAligner, AlignmentResult
from .metrics import PoseMetricsCalculator


@dataclass
class CheckpointResult:
    """Result of evaluating a single checkpoint."""
    checkpoint_id: str
    name: str
    score: float  # 0-1
    value: float  # Actual measured value
    ideal_value: float
    rating: str  # 'good', 'acceptable', 'poor'
    feedback: str
    frame_index: int  # Frame where this was evaluated


@dataclass
class AnalysisResult:
    """Complete analysis result for a skill attempt."""
    skill_name: str
    overall_score: float  # 0-100
    similarity_score: float  # How similar to reference (0-100)
    
    checkpoints: List[CheckpointResult]
    
    # Summary feedback
    strengths: List[str]
    improvements: List[str]
    
    # Detailed metrics
    joint_deviations: Dict[int, Dict[str, float]]
    
    # Alignment info
    alignment_distance: float
    
    # Metadata
    ref_video_frames: int
    user_video_frames: int
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'skill_name': self.skill_name,
            'overall_score': round(self.overall_score, 1),
            'similarity_score': round(self.similarity_score, 1),
            'checkpoints': [
                {
                    'id': cp.checkpoint_id,
                    'name': cp.name,
                    'score': round(cp.score * 100, 1),
                    'rating': cp.rating,
                    'feedback': cp.feedback,
                    'value': round(cp.value, 3),
                    'ideal': cp.ideal_value
                }
                for cp in self.checkpoints
            ],
            'strengths': self.strengths,
            'improvements': self.improvements,
            'metadata': {
                'ref_frames': self.ref_video_frames,
                'user_frames': self.user_video_frames,
                'alignment_distance': round(self.alignment_distance, 4)
            }
        }


class SkillAnalyzer:
    """
    Analyze soccer skill performance by comparing to reference.
    
    Usage:
        analyzer = SkillAnalyzer()
        result = analyzer.analyze_skill(
            skill_name='cruyff_turn',
            ref_video='reference.mp4',
            user_video='user_attempt.mp4'
        )
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize analyzer.
        
        Args:
            config_path: Path to skills.yaml config file
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / 'config' / 'skills.yaml'
        
        self.config_path = Path(config_path)
        self.skills_config = self._load_config()
        
        self.pose_extractor = PoseExtractor(model_complexity=2)
        self.aligner = SequenceAligner()
    
    def _load_config(self) -> dict:
        """Load skills configuration."""
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        else:
            # Return minimal default config
            return {
                'skills': {
                    'cruyff_turn': {
                        'name': 'Cruyff Turn',
                        'checkpoints': []
                    }
                },
                'landmarks': {}
            }
    
    def get_skill_config(self, skill_name: str) -> dict:
        """Get configuration for a specific skill."""
        if skill_name not in self.skills_config['skills']:
            raise ValueError(f"Unknown skill: {skill_name}")
        return self.skills_config['skills'][skill_name]
    
    def analyze_skill(
        self,
        skill_name: str,
        ref_video: str,
        user_video: str,
        normalize: bool = True
    ) -> AnalysisResult:
        """
        Analyze a user's skill attempt against a reference.
        
        Args:
            skill_name: Name of the skill (e.g., 'cruyff_turn')
            ref_video: Path to reference video
            user_video: Path to user's video
            normalize: Whether to normalize poses for comparison
        
        Returns:
            AnalysisResult with scores and feedback
        """
        skill_config = self.get_skill_config(skill_name)
        
        # Extract poses from both videos
        print(f"Extracting poses from reference video...")
        ref_poses = self.pose_extractor.extract_from_video(ref_video)
        
        print(f"Extracting poses from user video...")
        user_poses = self.pose_extractor.extract_from_video(user_video)
        
        # Normalize if requested
        if normalize:
            ref_poses = normalize_poses(ref_poses)
            user_poses = normalize_poses(user_poses)
        
        # Align sequences
        print("Aligning sequences...")
        alignment = self.aligner.align(ref_poses, user_poses)
        
        # Compute deviations
        deviations = self.aligner.compute_deviations(ref_poses, user_poses, alignment)
        
        # Create metrics calculators
        ref_metrics = PoseMetricsCalculator(ref_poses)
        user_metrics = PoseMetricsCalculator(user_poses)
        
        # Evaluate checkpoints
        checkpoint_results = self._evaluate_checkpoints(
            skill_config,
            ref_poses,
            user_poses,
            ref_metrics,
            user_metrics,
            alignment
        )
        
        # Calculate overall score
        similarity_score = self.aligner.get_similarity_score(alignment)
        checkpoint_score = self._calculate_checkpoint_score(checkpoint_results)
        
        # Weight: 70% checkpoints, 30% similarity
        overall_score = 0.7 * checkpoint_score + 0.3 * similarity_score
        
        # Generate summary feedback
        strengths, improvements = self._generate_summary_feedback(checkpoint_results)
        
        # Summarize deviations
        deviation_summary = {}
        for joint_idx, devs in deviations.items():
            deviation_summary[joint_idx] = {
                'mean': float(np.mean(devs)),
                'max': float(np.max(devs)),
                'std': float(np.std(devs))
            }
        
        return AnalysisResult(
            skill_name=skill_config['name'],
            overall_score=overall_score,
            similarity_score=similarity_score,
            checkpoints=checkpoint_results,
            strengths=strengths,
            improvements=improvements,
            joint_deviations=deviation_summary,
            alignment_distance=alignment.normalized_distance,
            ref_video_frames=len(ref_poses),
            user_video_frames=len(user_poses)
        )
    
    def _evaluate_checkpoints(
        self,
        skill_config: dict,
        ref_poses: PoseSequence,
        user_poses: PoseSequence,
        ref_metrics: PoseMetricsCalculator,
        user_metrics: PoseMetricsCalculator,
        alignment: AlignmentResult
    ) -> List[CheckpointResult]:
        """Evaluate all checkpoints for a skill."""
        results = []
        
        checkpoints = skill_config.get('checkpoints', [])
        
        for checkpoint in checkpoints:
            result = self._evaluate_single_checkpoint(
                checkpoint,
                ref_poses,
                user_poses,
                ref_metrics,
                user_metrics,
                alignment
            )
            results.append(result)
        
        return results
    
    def _evaluate_single_checkpoint(
        self,
        checkpoint: dict,
        ref_poses: PoseSequence,
        user_poses: PoseSequence,
        ref_metrics: PoseMetricsCalculator,
        user_metrics: PoseMetricsCalculator,
        alignment: AlignmentResult
    ) -> CheckpointResult:
        """Evaluate a single checkpoint."""
        checkpoint_id = checkpoint['id']
        metric_type = checkpoint.get('metric_type', 'distance')
        ideal_value = checkpoint.get('ideal_value', 0)
        threshold_good = checkpoint.get('threshold_good', ideal_value * 0.9)
        threshold_acceptable = checkpoint.get('threshold_acceptable', ideal_value * 0.7)
        
        # Find key frame (use middle of sequence as default)
        # In full implementation, this would be based on phase detection
        ref_key_frame = len(ref_poses) // 2
        user_key_frame = alignment.get_user_frame_for_ref(ref_key_frame)
        
        # Calculate metric based on type
        measured_value = self._calculate_checkpoint_metric(
            checkpoint,
            user_metrics,
            user_key_frame
        )
        
        # Determine rating
        if metric_type in ['rotation_angle', 'angle']:
            # For angles, closer to ideal is better
            diff = abs(measured_value - ideal_value)
            diff_good = abs(threshold_good - ideal_value)
            diff_acceptable = abs(threshold_acceptable - ideal_value)
            
            if diff <= diff_good:
                rating = 'good'
                score = 1.0
            elif diff <= diff_acceptable:
                rating = 'acceptable'
                score = 0.7
            else:
                rating = 'poor'
                score = max(0.3, 1.0 - (diff / ideal_value))
        else:
            # For distance metrics, lower is usually better
            if measured_value <= threshold_good:
                rating = 'good'
                score = 1.0
            elif measured_value <= threshold_acceptable:
                rating = 'acceptable'
                score = 0.7
            else:
                rating = 'poor'
                score = 0.3
        
        # Get feedback text
        feedback_config = checkpoint.get('feedback', {})
        feedback = feedback_config.get(rating, f"Work on your {checkpoint['name'].lower()}")
        
        return CheckpointResult(
            checkpoint_id=checkpoint_id,
            name=checkpoint['name'],
            score=score,
            value=measured_value,
            ideal_value=ideal_value,
            rating=rating,
            feedback=feedback,
            frame_index=user_key_frame
        )
    
    def _calculate_checkpoint_metric(
        self,
        checkpoint: dict,
        metrics: PoseMetricsCalculator,
        frame_index: int
    ) -> float:
        """Calculate the metric value for a checkpoint."""
        metric_type = checkpoint.get('metric_type', 'distance')
        joints = checkpoint.get('joints', [])
        
        if metric_type == 'rotation_angle':
            # Hip rotation
            rotations = metrics.get_hip_rotation_over_time()
            # Get rotation at key frame (or max rotation in window)
            window = 10
            start = max(0, frame_index - window)
            end = min(len(rotations), frame_index + window)
            return float(np.max(np.abs(rotations[start:end])))
        
        elif metric_type == 'angle':
            # Joint angle (e.g., knee)
            if 'knee' in checkpoint['id']:
                return metrics.get_knee_angle(frame_index, side='left')
            elif 'elbow' in checkpoint['id']:
                return metrics.get_elbow_angle(frame_index, side='left')
            return 90.0  # Default
        
        elif metric_type == 'distance':
            # Distance from reference point
            return metrics.get_plant_foot_distance(frame_index, plant_side='left')
        
        elif metric_type == 'direction_change':
            # Shoulder fake
            change, _ = metrics.get_shoulder_direction_change()
            return change
        
        elif metric_type == 'velocity_change':
            # Exit speed
            velocities = metrics.get_center_of_mass_velocity()
            if len(velocities) > frame_index + 5:
                return float(velocities[frame_index + 5] / (velocities[frame_index] + 0.01))
            return 1.0
        
        elif metric_type == 'arc_completeness':
            return metrics.get_foot_arc_completeness()
        
        else:
            return 0.0
    
    def _calculate_checkpoint_score(self, results: List[CheckpointResult]) -> float:
        """Calculate weighted average of checkpoint scores."""
        if not results:
            return 50.0  # Default middle score
        
        total_score = sum(r.score * 100 for r in results)
        return total_score / len(results)
    
    def _generate_summary_feedback(
        self,
        results: List[CheckpointResult]
    ) -> tuple:
        """Generate summary strengths and improvements lists."""
        strengths = []
        improvements = []
        
        for result in results:
            if result.rating == 'good':
                strengths.append(result.feedback)
            elif result.rating == 'poor':
                improvements.append(result.feedback)
        
        # Add generic encouragement if needed
        if not strengths:
            strengths.append("Good effort! Keep practicing.")
        if not improvements:
            improvements.append("Looking great! Focus on consistency.")
        
        return strengths, improvements
    
    def analyze_skill_comparative(
        self,
        skill_name: str,
        ref_video: str,
        user_video: str,
        normalize: bool = True
    ) -> AnalysisResult:
        """
        Analyze a user's skill attempt using comparative scoring.
        
        Scores user relative to reference performance instead of absolute ideals.
        Reference video should score ~95-100.
        
        Args:
            skill_name: Name of the skill (e.g., 'cruyff_turn')
            ref_video: Path to reference video
            user_video: Path to user's video
            normalize: Whether to normalize poses for comparison
        
        Returns:
            AnalysisResult with comparative scores
        """
        from .comparative_analyzer import ComparativeScoring, analyze_skill_comparative as comp_analyze
        
        skill_config = self.get_skill_config(skill_name)
        
        # Extract poses from both videos
        print(f"Extracting poses from reference video...")
        ref_poses = self.pose_extractor.extract_from_video(ref_video)
        
        print(f"Extracting poses from user video...")
        user_poses = self.pose_extractor.extract_from_video(user_video)
        
        # Normalize if requested
        if normalize:
            ref_poses = normalize_poses(ref_poses)
            user_poses = normalize_poses(user_poses)
        
        # Align sequences
        print("Aligning sequences...")
        alignment = self.aligner.align(ref_poses, user_poses)
        
        # Use comparative analyzer (pass self to avoid creating new analyzer)
        comp_result = comp_analyze(skill_config, ref_poses, user_poses, alignment, analyzer_instance=self)
        
        # Convert comparative checkpoints to CheckpointResult format
        checkpoint_results = []
        for comp_cp in comp_result['checkpoints']:
            checkpoint_results.append(CheckpointResult(
                checkpoint_id=comp_cp.checkpoint_id,
                name=comp_cp.name,
                score=comp_cp.score / 100.0,  # Convert to 0-1 range
                value=comp_cp.user_value,
                ideal_value=comp_cp.ref_value,  # Reference value becomes "ideal"
                rating=comp_cp.rating,
                feedback=comp_cp.feedback,
                frame_index=len(user_poses) // 2  # Approximate key frame
            ))
        
        # Generate summary feedback
        strengths, improvements = self._generate_summary_feedback(checkpoint_results)
        
        # Compute deviations for metadata
        deviations = self.aligner.compute_deviations(ref_poses, user_poses, alignment)
        deviation_summary = {}
        for joint_idx, devs in deviations.items():
            deviation_summary[joint_idx] = {
                'mean': float(np.mean(devs)),
                'max': float(np.max(devs)),
                'std': float(np.std(devs))
            }
        
        return AnalysisResult(
            skill_name=skill_config['name'],
            overall_score=comp_result['overall_score'],
            similarity_score=comp_result['similarity_score'],
            checkpoints=checkpoint_results,
            strengths=strengths,
            improvements=improvements,
            joint_deviations=deviation_summary,
            alignment_distance=alignment.normalized_distance,
            ref_video_frames=len(ref_poses),
            user_video_frames=len(user_poses)
        )
    
    def analyze_from_poses(
        self,
        skill_name: str,
        ref_poses: PoseSequence,
        user_poses: PoseSequence,
        normalize: bool = True
    ) -> AnalysisResult:
        """
        Analyze when poses are already extracted.
        
        Useful for batch processing or when poses come from different sources.
        """
        skill_config = self.get_skill_config(skill_name)
        
        if normalize:
            ref_poses = normalize_poses(ref_poses)
            user_poses = normalize_poses(user_poses)
        
        # Same analysis flow as analyze_skill
        alignment = self.aligner.align(ref_poses, user_poses)
        deviations = self.aligner.compute_deviations(ref_poses, user_poses, alignment)
        
        ref_metrics = PoseMetricsCalculator(ref_poses)
        user_metrics = PoseMetricsCalculator(user_poses)
        
        checkpoint_results = self._evaluate_checkpoints(
            skill_config, ref_poses, user_poses,
            ref_metrics, user_metrics, alignment
        )
        
        similarity_score = self.aligner.get_similarity_score(alignment)
        checkpoint_score = self._calculate_checkpoint_score(checkpoint_results)
        overall_score = 0.7 * checkpoint_score + 0.3 * similarity_score
        
        strengths, improvements = self._generate_summary_feedback(checkpoint_results)
        
        deviation_summary = {
            joint_idx: {
                'mean': float(np.mean(devs)),
                'max': float(np.max(devs)),
                'std': float(np.std(devs))
            }
            for joint_idx, devs in deviations.items()
        }
        
        return AnalysisResult(
            skill_name=skill_config['name'],
            overall_score=overall_score,
            similarity_score=similarity_score,
            checkpoints=checkpoint_results,
            strengths=strengths,
            improvements=improvements,
            joint_deviations=deviation_summary,
            alignment_distance=alignment.normalized_distance,
            ref_video_frames=len(ref_poses),
            user_video_frames=len(user_poses)
        )
    
    def close(self):
        """Release resources."""
        self.pose_extractor.close()


# Convenience function
def analyze_skill(
    skill_name: str,
    ref_video: str,
    user_video: str,
    config_path: Optional[str] = None
) -> AnalysisResult:
    """
    Quick function to analyze a skill.
    
    Args:
        skill_name: Skill to analyze
        ref_video: Reference video path
        user_video: User video path
        config_path: Optional config file path
    
    Returns:
        AnalysisResult
    """
    analyzer = SkillAnalyzer(config_path)
    try:
        return analyzer.analyze_skill(skill_name, ref_video, user_video)
    finally:
        analyzer.close()


if __name__ == "__main__":
    print("Analyzer module loaded successfully.")
    print("\nTo analyze a skill, use:")
    print("  from footylevels.analyzer import analyze_skill")
    print("  result = analyze_skill('cruyff_turn', 'ref.mp4', 'user.mp4')")
    print("  print(result.to_dict())")
