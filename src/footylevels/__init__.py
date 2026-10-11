"""
FootyLevels - 3D body extraction for soccer skill video

Wraps Meta's SAM 3D Body (MHR) to turn skill footage into per-frame 3D body
reconstructions.
"""

from importlib import import_module

# Keep metadata/authentication processes independent of the GPU/media stack,
# while preserving the semester package's public imports for existing callers.
_MODULE_EXPORTS = {
    "body_extractor": ("BodyDetection", "BodyFrame", "BodySequence", "SAM3DBodyExtractor", "extract_bodies", "resolve_device"),
    "catalog": ("SkillCatalog",),
    "mesh": ("orient_y_up", "write_mesh", "write_obj", "write_ply"),
    "reconstruct": ("ImageReconstructor", "ReconstructionError"),
    "metrics": ("calculate_angle_2vectors", "calculate_angle_3points", "calculate_angular_velocity", "calculate_distance", "calculate_velocity"),
    "storage": ("SequenceStorage", "save_sequence"),
    "usdz": ("write_usdz", "write_usdz_animation"),
    "video_processor": ("VideoInfo", "VideoProcessor"),
    "visualizer": ("BodyVisualizer",),
}


def __getattr__(name):
    for module, exports in _MODULE_EXPORTS.items():
        if name in exports:
            value = getattr(import_module("." + module, __name__), name)
            globals()[name] = value
            return value
    raise AttributeError(name)


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
    # Mesh export
    "write_mesh",
    "write_obj",
    "write_ply",
    "orient_y_up",
    "write_usdz",
    "write_usdz_animation",
    # Reconstruction
    "ImageReconstructor",
    "ReconstructionError",
    # Storage
    "SequenceStorage",
    "save_sequence",
    # Video + visualization
    "VideoInfo",
    "VideoProcessor",
    "BodyVisualizer",
]
