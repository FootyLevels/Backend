"""
FootyLevels - AI Soccer Skill Analysis
"""

from .pose_extractor import PoseExtractor, PoseSequence, normalize_poses, LandmarkIndex
from .alignment import SequenceAligner, align_sequences, AlignmentResult
from .metrics import PoseMetricsCalculator, calculate_angle_3points, calculate_distance
from .analyzer import SkillAnalyzer, analyze_skill, AnalysisResult
from .feedback import FeedbackGenerator, generate_feedback, StructuredFeedback
from .storage import AnalysisStorage, save_analysis_result

__version__ = "0.1.0"
__all__ = [
    "PoseExtractor",
    "PoseSequence",
    "normalize_poses",
    "LandmarkIndex",
    "SequenceAligner",
    "align_sequences",
    "AlignmentResult",
    "PoseMetricsCalculator",
    "calculate_angle_3points",
    "calculate_distance",
    "SkillAnalyzer",
    "analyze_skill",
    "AnalysisResult",
    "FeedbackGenerator",
    "generate_feedback",
    "StructuredFeedback",
    "AnalysisStorage",
    "save_analysis_result",
]
