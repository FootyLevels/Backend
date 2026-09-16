"""
FootyLevels - 3D body extraction for soccer skill video

Wraps Meta's SAM 3D Body (MHR) to turn skill footage into per-frame 3D body
reconstructions.
"""

from .body_extractor import (
    BodyDetection,
    BodyFrame,
    BodySequence,
    SAM3DBodyExtractor,
    extract_bodies,
    resolve_device,
)
from .catalog import SkillCatalog
from .metrics import (
    calculate_angle_2vectors,
    calculate_angle_3points,
    calculate_angular_velocity,
    calculate_distance,
    calculate_velocity,
)
from .storage import SequenceStorage, save_sequence
from .video_processor import VideoInfo, VideoProcessor
from .visualizer import BodyVisualizer

__version__ = "0.2.0"
__all__ = [
    # Extraction
    "SAM3DBodyExtractor",
    "extract_bodies",
    "resolve_device",
    "BodyDetection",
    "BodyFrame",
    "BodySequence",
    # Catalog
    "SkillCatalog",
    # Metrics
    "calculate_angle_3points",
    "calculate_angle_2vectors",
    "calculate_distance",
    "calculate_velocity",
    "calculate_angular_velocity",
    # Storage
    "SequenceStorage",
    "save_sequence",
    # Video + visualization
    "VideoInfo",
    "VideoProcessor",
    "BodyVisualizer",
]
