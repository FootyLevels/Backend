"""Stable reconstruction input identity shared by the API and private adapter."""
import hashlib
import json

BODY_PROFILE = "body-keyframes-v1"
MOTION_PROFILE = "body-motion-30fps-v1"
LESSON_PROFILE = "lesson-3d-v1"
LESSON_MOTION_PROFILE = "lesson-motion-v1"


def body_input_hash(source, keyframes):
    return hashlib.sha256(json.dumps({"profile": BODY_PROFILE, "sourceSha256": source.sha256,
        "keyframes": [frame.model_dump(mode="json", by_alias=True) for frame in keyframes]},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def motion_input_hash(source, segment):
    return hashlib.sha256(json.dumps({"profile": MOTION_PROFILE, "sourceSha256": source.sha256,
        "segment": segment.model_dump(mode="json", by_alias=True)},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def lesson_input_hash(source, model):
    """One lesson per prepared source, lesson profile and authoring model."""
    return hashlib.sha256(json.dumps({"profile": LESSON_PROFILE, "motionProfile": LESSON_MOTION_PROFILE,
        "sourceSha256": source.sha256, "model": model},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
