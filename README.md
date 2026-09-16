# FootyLevels ⚽

3D body extraction for soccer skill video, powered by Meta's
[SAM 3D Body](https://github.com/facebookresearch/sam-3d-body).

Point it at a clip of a skill move and get a per-frame 3D reconstruction of the
player's body, built on the Momentum Human Rig (MHR).

> **Scoring is not implemented.** This repo currently does extraction only.
> The previous MediaPipe-based scoring, feedback, and DTW alignment layers were
> removed — see the git history if you need them back.

## Pipeline

```
video → SAM 3D Body (per frame) → BodySequence → store / visualize
            ↓
      MHR mesh + 3D joints
```

SAM 3D Body is a **single-image** model, so video is handled by running one
forward pass per frame. There is no temporal smoothing and no tracking.

## Setup

### 1. Python environment

Upstream targets **Python 3.11**.

```bash
uv venv --python 3.11
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Install SAM 3D Body

It is not on PyPI. Follow
[INSTALL.md](https://github.com/facebookresearch/sam-3d-body/blob/main/INSTALL.md)
upstream — it needs a platform-matched torch build and pulls detectron2 from git.

### 3. Download the checkpoint

The weights are **gated**: request access on the
[model page](https://huggingface.co/facebook/sam-3d-body-dinov3) and accept the
licence first. Then (~2.8 GB):

```bash
hf download facebook/sam-3d-body-dinov3 \
    --local-dir models/checkpoints/sam-3d-body-dinov3
```

Confirm it landed:

```bash
python scripts/demo.py --check
```

`models/` is gitignored — never commit the weights.

## Usage

### CLI

```bash
# Check checkpoint status and which device will be used
python scripts/demo.py --check

# Extract from a video file
python scripts/demo.py --video data/reference/cruyff_turn.mp4

# Extract from a skill's stored reference video, every 3rd frame
python scripts/demo.py --skill cruyff_turn --stride 3

# Extract and write an annotated overlay video
python scripts/demo.py --video my_clip.mp4 --render out/annotated.mp4

# List everything extracted so far
python scripts/demo.py --list
```

Inference is slow — an 840M-parameter forward pass per frame. Use `--stride` and
`--max-frames` while iterating, and `--device` to override auto-detection.

### Library

```python
from footylevels import SAM3DBodyExtractor, SequenceStorage

extractor = SAM3DBodyExtractor()
sequence = extractor.extract_from_video("data/reference/cruyff_turn.mp4", stride=2)

print(sequence.summary())
print(sequence.keypoints_3d().shape)     # (frames, joints, 3) metres, camera frame
print(sequence.joint_trajectory(5).shape)  # (frames, 3)

SequenceStorage().save_sequence(sequence, name="cruyff_turn")
```

### API

```bash
source .venv/bin/activate
cd api && python -m uvicorn main:app --reload
```

| Endpoint | Purpose |
|---|---|
| `GET /api/model/status` | Checkpoint present? Which device? |
| `POST /api/extract` | Queue extraction for an uploaded video |
| `GET /api/extract/{job_id}` | Poll a queued extraction |
| `GET /api/sequences` | List stored sequences |
| `GET /api/sequences/{id}` | One sequence's metadata |
| `GET /api/sequences/{id}/keypoints` | Its 3D + 2D keypoints |
| `DELETE /api/sequences/{id}` | Delete a sequence |
| `GET /api/skills`, `/api/skills/{id}` | Skill catalog |
| `GET /api/players`, `/api/players/{id}` | Player catalog |
| `GET /api/categories`, `/api/progression` | Browsing structure |

Extraction is queued rather than inline, because a short clip can still take
minutes:

```bash
curl -X POST http://localhost:8000/api/extract \
  -F "video=@my_clip.mp4" -F "name=my_attempt" -F "stride=3"
# -> {"job_id": "...", "status": "queued", ...}

curl http://localhost:8000/api/extract/<job_id>
```

The job table is in-memory — it does not survive a restart. Swap in Redis or a
database before this runs anywhere real.

## Output format

Per detected person, per frame, SAM 3D Body returns:

| Field | Meaning |
|---|---|
| `keypoints_3d` | `(J, 3)` camera-frame joints in **metres**, including camera translation |
| `keypoints_2d` | `(J, 2)` pixels on the source image |
| `vertices` | `(18439, 3)` MHR mesh — opt-in via `include_vertices` |
| `cam_t`, `focal_length` | Estimated camera parameters |
| `shape_params`, `body_pose_params`, `hand_pose_params` | Interpretable MHR parameters |

Joint count and ordering come from the loaded MHR asset and are **not** hardcoded
anywhere in this repo. To work out which indices matter for a given skill, render
with labels:

```bash
python scripts/demo.py --video clip.mp4 --render out.mp4 --label-joints
```

Sequences are stored as a compressed `.npz` of arrays plus a sibling `.json` of
metadata, under `data/sequences/`. Only the primary (largest) subject per frame
is persisted.

## Project structure

```
Backend/
├── src/footylevels/
│   ├── body_extractor.py   # SAM 3D Body wrapper, BodySequence data model
│   ├── catalog.py          # Skill/player catalog reader
│   ├── storage.py          # Sequence persistence (npz + json)
│   ├── video_processor.py  # Video IO utilities
│   ├── visualizer.py       # Keypoint overlays, trajectory plots
│   └── metrics.py          # Geometry/kinematics helpers
├── api/main.py             # FastAPI server
├── config/skills.yaml      # Catalog metadata only
├── scripts/demo.py         # Extraction CLI
├── models/checkpoints/     # SAM 3D Body weights (gitignored)
└── data/
    ├── reference/          # Reference videos
    └── sequences/          # Extracted output (gitignored)
```

## Development

```bash
pytest tests/ -v
```

The suite runs without SAM 3D Body installed and without the checkpoint — it
covers the data model, output parsing, storage round-trip, and catalog integrity.

## Known gaps

- **`data/reference/elastico.mp4` is a byte-identical copy of `cruyff_turn.mp4`.**
  It needs replacing with real elastico footage.
- No reference videos for the other six skills in the catalog.
- `scripts/download_sample_videos.py` still points at `example.com` placeholders.
- The catalog's `video_url` / `image_url` / `thumbnail_url` fields reference
  assets that do not exist in this repo and are not served by the API.

## Credits

- 3D body model: [SAM 3D Body](https://github.com/facebookresearch/sam-3d-body) /
  [MHR](https://github.com/facebookresearch/MHR), Meta Superintelligence Labs
