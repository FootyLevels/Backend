"""Adapter for Meta's official SAM3DBodyEstimator, loaded once per worker."""
import os
import sys
from pathlib import Path

import cv2
import numpy as np

from ..mesh import export_mesh


class LocalProvider:
    _estimator = None

    @classmethod
    def release(cls):
        """The serial motion runner releases the warm API model before subprocesses."""
        import gc
        import torch
        cls._estimator = None
        gc.collect()
        torch.cuda.empty_cache()

    def load(self):
        if self._estimator is None:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError("Meta's current estimator requires NVIDIA CUDA; use the fal provider on this Mac.")
            repo = Path(os.environ["SAM3D_REPO"]).resolve()
            checkpoint = Path(os.environ["SAM3D_CHECKPOINT"]).resolve()
            sys.path.insert(0, str(repo))
            from sam_3d_body import SAM3DBodyEstimator, load_sam_3d_body
            model, cfg = load_sam_3d_body(checkpoint_path=str(checkpoint), device="cuda",
                                        mhr_path=str(checkpoint.parent / "assets/mhr_model.pt"))
            # Explicit subject boxes avoid a separate detector checkpoint and identity swaps.
            type(self)._estimator = SAM3DBodyEstimator(sam_3d_body_model=model, model_cfg=cfg)
        return self._estimator

    def reconstruct(self, image: Path, bbox: list[float], destination: Path) -> dict:
        estimator = self.load()
        bgr = cv2.imread(str(image))
        if bgr is None:
            raise ValueError("Cannot read key frame")
        height, width = bgr.shape[:2]
        box = np.asarray(bbox, dtype=np.float32) * [width, height, width, height]
        outputs = estimator.process_one_image(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                                              bboxes=box.reshape(1, 4), inference_type="full")
        if not outputs:
            raise RuntimeError("SAM 3D Body returned no person for this crop")
        person = outputs[0]
        return export_mesh(person["pred_vertices"], estimator.faces, destination, metadata={
            "model": "Meta SAM 3D Body", "provider": "local", "bboxPixels": box.tolist(),
            "cameraTranslation": np.asarray(person["pred_cam_t"]).tolist(),
            "focalLength": float(np.asarray(person["focal_length"]).reshape(-1)[0]),
            "keypoints2d": np.asarray(person["pred_keypoints_2d"]).tolist(),
            "keypoints3dCamera": np.asarray(person["pred_keypoints_3d"]).tolist(),
            "cameraEstimate": "default FOV; no calibrated camera", "groundAligned": False,
        })
