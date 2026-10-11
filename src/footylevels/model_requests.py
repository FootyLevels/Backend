"""Stable reconstruction input identity shared by the API and private adapter."""
import hashlib
import json

BODY_PROFILE = "body-keyframes-v1"


def body_input_hash(source, keyframes):
    return hashlib.sha256(json.dumps({"profile": BODY_PROFILE, "sourceSha256": source.sha256,
        "keyframes": [frame.model_dump(mode="json", by_alias=True) for frame in keyframes]},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
