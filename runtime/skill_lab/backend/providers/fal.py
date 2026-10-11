"""Official fal SAM 3D Body endpoint. Keys never reach the browser."""
import base64
import io
import tempfile
from pathlib import Path

import httpx
from PIL import Image

from ..mesh import normalize_file

ENDPOINT = "fal-ai/sam-3/3d-body"


def image_data_uri(path: Path, bbox: list[float]) -> str:
    with Image.open(path) as image:
        w, h = image.size
        crop = image.crop((int(bbox[0] * w), int(bbox[1] * h),
                           round(bbox[2] * w), round(bbox[3] * h)))
        output = io.BytesIO()
        crop.convert("RGB").save(output, format="JPEG", quality=96)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()


class FalProvider:
    def reconstruct(self, image: Path, bbox: list[float], destination: Path) -> dict:
        import fal_client
        # Queue SDK owns authentication and polls a single submitted request.
        result = fal_client.subscribe(ENDPOINT, arguments={
            "image_url": image_data_uri(image, bbox), "export_meshes": True,
            "include_3d_keypoints": False, "include_mhr_params": False,
        })
        people = result.get("metadata", {}).get("people", [])
        if len(people) != 1:
            raise RuntimeError(f"Detected {len(people)} people. Tighten the subject box and try again.")
        output = result["model_glb"]
        url = output if isinstance(output, str) else output["url"]
        # Result is a GLB in renderer coordinates. Recenter without flipping again.
        with httpx.Client(follow_redirects=True, timeout=120) as client:
            response = client.get(url)
            response.raise_for_status()
        if len(response.content) > 100 * 1024 * 1024 or response.content[:4] != b"glTF":
            raise ValueError("Provider did not return a valid GLB under 100 MB")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "result.glb"
            source.write_bytes(response.content)
            return normalize_file(source, destination, metadata={
                "model": "Meta SAM 3D Body", "provider": "fal", "endpoint": ENDPOINT,
                "person": people[0], "subjectCrop": bbox,
                "cameraEstimate": "provider estimated", "groundAligned": False,
            })
