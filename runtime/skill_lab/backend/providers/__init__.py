import os
from pathlib import Path


def capabilities() -> list[dict]:
    local_reason = "Set SAM3D_REPO and SAM3D_CHECKPOINT on a machine with an NVIDIA CUDA GPU."
    local_ready = False
    repo, checkpoint = os.getenv("SAM3D_REPO"), os.getenv("SAM3D_CHECKPOINT")
    if repo and checkpoint:
        try:
            import torch
            local_ready = (torch.cuda.is_available() and (Path(repo) / "sam_3d_body").is_dir()
                           and Path(checkpoint).is_file()
                           and (Path(checkpoint).parent / "assets/mhr_model.pt").is_file())
            if not local_ready:
                local_reason = "CUDA or SAM 3D Body checkpoint assets are unavailable."
        except ImportError:
            local_reason = "Install the SAM 3D Body CUDA environment first."
    return [
        {"id": "local", "label": "Local CUDA", "ready": local_ready,
         "reason": None if local_ready else local_reason, "remote": False},
        {"id": "fal", "label": "fal hosted", "ready": bool(os.getenv("FAL_KEY")),
         "reason": None if os.getenv("FAL_KEY") else "Set FAL_KEY in the server environment, then restart the server.",
         "remote": True},
    ]


def get_provider(name: str):
    if name == "local":
        from .local import LocalProvider
        return LocalProvider()
    if name == "fal":
        from .fal import FalProvider
        return FalProvider()
    raise ValueError("Unknown inference provider")
