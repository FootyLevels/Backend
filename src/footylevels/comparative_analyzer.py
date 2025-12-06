"""
Comparative Analyzer Module
Score users relative to reference performance instead of absolute ideals.
"""

import numpy as np
from typing import Dict, List
from dataclasses import dataclass

from .pose_extractor import PoseSequence
from .alignment import AlignmentResult
from .metrics import PoseMetricsCalculator


@dataclass
class ComparativeCheckpoint:
    """Checkpoint evaluated comparatively against reference."""
    checkpoint_id: str
    name: str
    user_value: float
    ref_value: float
    deviation_percent: float
    score: float  # 0-100
    rating: str
    feedback: str


class ComparativeScoring:
    """
    Score user performance relative to reference.

    Key insight: Reference video should score ~95-100.
    User scores based on how close they are to reference.
    """

    # Tolerance levels (how much deviation is acceptable)
    EXCELLENT_THRESHOLD = 0.10  # Within 10% of reference
    GOOD_THRESHOLD = 0.20       # Within 20% of reference
    ACCEPTABLE_THRESHOLD = 0.35 # Within 35% of reference

    @staticmethod
    def calculate_deviation_score(
        user_value: float,
        ref_value: float,
        metric_type: str = 'distance'
    ) -> tuple[float, float, str]:
        """
        Calculate score based on deviation from reference.

        Args:
            user_value: User's measured value
            ref_value: Reference measured value
            metric_type: Type of metric (affects scoring logic)

        Returns:
            (score, deviation_percent, rating)
        """
        # Handle edge cases
        if ref_value == 0:
            ref_value = 0.001

        # Calculate percentage deviation
        deviation = abs(user_value - ref_value) / abs(ref_value)

        # Score based on deviation
        if deviation <= ComparativeScoring.EXCELLENT_THRESHOLD:
            score = 100 - (deviation * 100)  # 90-100 range
            rating = 'excellent'
        elif deviation <= ComparativeScoring.GOOD_THRESHOLD:
            score = 85 - ((deviation - 0.10) * 150)  # 70-90 range
            rating = 'good'
        elif deviation <= ComparativeScoring.ACCEPTABLE_THRESHOLD:
            score = 60 - ((deviation - 0.20) * 100)  # 50-70 range
            rating = 'acceptable'
        else:
            score = max(30, 50 - (deviation * 50))  # 30-50 range
            rating = 'poor'

        return score, deviation * 100, rating

    @staticmethod
    def generate_comparative_feedback(
        checkpoint_id: str,
        user_value: float,
        ref_value: float,
        rating: str,
        checkpoint_config: dict
    ) -> str:
        """Generate feedback based on comparison to reference."""

        deviation_percent = abs((user_value - ref_value) / ref_value * 100)

        if rating == 'excellent':
            return checkpoint_config.get('feedback', {}).get(
                'good',
                f"Excellent! Your {checkpoint_config['name']} matches the reference closely."
            )
        elif rating == 'good':
            return checkpoint_config.get('feedback', {}).get(
                'acceptable',
                f"Good {checkpoint_config['name']}! You're close to the reference."
            )
        elif rating == 'acceptable':
            if user_value > ref_value:
                direction = "reduce"
            else:
                direction = "increase"
            return f"Try to {direction} your {checkpoint_config['name']} slightly to match the reference better."
        else:
            return checkpoint_config.get('feedback', {}).get(
                'poor',
                f"Focus on matching the reference's {checkpoint_config['name']}. You're {deviation_percent:.0f}% off."
            )


def analyze_skill_comparative(
    skill_config: dict,
    ref_poses: PoseSequence,
    user_poses: PoseSequence,
    alignment: AlignmentResult,
    analyzer_instance=None
) -> Dict:
    """
    Analyze skill using comparative scoring.

    Reference performance establishes the benchmark.
    User is scored relative to reference.

    Args:
        skill_config: Skill configuration
        ref_poses: Reference pose sequence
        user_poses: User pose sequence
        alignment: DTW alignment result
        analyzer_instance: Optional SkillAnalyzer instance to use for metric calculation

    Returns:
        Analysis results dict
    """
    from .analyzer import SkillAnalyzer

    ref_metrics = PoseMetricsCalculator(ref_poses)
    user_metrics = PoseMetricsCalculator(user_poses)

    # Use provided analyzer or create a temporary one
    if analyzer_instance is None:
        analyzer_instance = SkillAnalyzer()

    checkpoints = []

    # Evaluate each checkpoint comparatively
    for checkpoint_cfg in skill_config.get('checkpoints', []):
        # Get key frame
        ref_key_frame = len(ref_poses) // 2
        user_key_frame = alignment.get_user_frame_for_ref(ref_key_frame)

        # Calculate metrics for both
        ref_value = analyzer_instance._calculate_checkpoint_metric(
            checkpoint_cfg, ref_metrics, ref_key_frame
        )
        user_value = analyzer_instance._calculate_checkpoint_metric(
            checkpoint_cfg, user_metrics, user_key_frame
        )

        # Comparative scoring
        score, deviation_pct, rating = ComparativeScoring.calculate_deviation_score(
            user_value, ref_value, checkpoint_cfg.get('metric_type', 'distance')
        )

        feedback = ComparativeScoring.generate_comparative_feedback(
            checkpoint_cfg['id'],
            user_value,
            ref_value,
            rating,
            checkpoint_cfg
        )

        checkpoints.append(ComparativeCheckpoint(
            checkpoint_id=checkpoint_cfg['id'],
            name=checkpoint_cfg['name'],
            user_value=user_value,
            ref_value=ref_value,
            deviation_percent=deviation_pct,
            score=score,
            rating=rating,
            feedback=feedback
        ))

    # Overall score
    if checkpoints:
        overall_score = sum(c.score for c in checkpoints) / len(checkpoints)
    else:
        overall_score = 70.0

    # Add similarity bonus (from DTW alignment)
    from .alignment import SequenceAligner
    aligner = SequenceAligner()
    similarity_score = aligner.get_similarity_score(alignment)

    # Weighted: 80% checkpoints, 20% similarity
    final_score = 0.8 * overall_score + 0.2 * similarity_score

    return {
        'overall_score': final_score,
        'checkpoint_score': overall_score,
        'similarity_score': similarity_score,
        'checkpoints': checkpoints,
        'rating': _get_rating(final_score)
    }


def _get_rating(score: float) -> str:
    """Convert score to rating."""
    if score >= 90:
        return 'excellent'
    elif score >= 75:
        return 'good'
    elif score >= 60:
        return 'acceptable'
    else:
        return 'needs_work'


# Quick test
if __name__ == "__main__":
    print("Comparative Analyzer loaded successfully.")
    print("\nKey principle:")
    print("  Reference video = benchmark (95-100 score)")
    print("  User scored based on deviation from reference")
    print("  - Within 10% = Excellent (90-100)")
    print("  - Within 20% = Good (75-90)")
    print("  - Within 35% = Acceptable (60-75)")
    print("  - Beyond 35% = Needs work (<60)")
