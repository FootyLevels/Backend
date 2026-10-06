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

It is **not on PyPI and has no setup.py** — `pip install git+...` fails. Clone
it and put the repo root on `sys.path`:

```bash
git clone https://github.com/facebookresearch/sam-3d-body.git vendor/sam-3d-body
git -C vendor/sam-3d-body checkout b5c765a

# make it importable
echo "$(pwd)/vendor/sam-3d-body" \
  > "$(python -c 'import site;print(site.getsitepackages()[0])')/sam_3d_body_repo.pth"

# detectron2 from the pinned commit
pip install 'git+https://github.com/facebookresearch/detectron2.git@a1ce2f9' \
    --no-build-isolation --no-deps
```

`vendor/` is gitignored. detectron2 builds fine on Apple Silicon.

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

### Photo to 3D model

Drop photos into `data/reference/pictures_for_model/`, then:

```bash
# Process every picture in the folder
python main.py

# One picture, saving a 2D keypoint overlay too
python main.py --image player.jpg --preview

# PLY instead of OBJ
python main.py --format ply

# If the mesh loads upside down in your viewer
python main.py --y-up
```

Meshes are written to `data/output/meshes/` — one file per detected person,
18,439 vertices and 36,874 faces each (~1.2 MB as OBJ, 0.54 MB as USDZ). Open them in Preview, Blender, or
[3dviewer.net](https://3dviewer.net).

This is the natural fit for SAM 3D Body: one photo is one forward pass, unlike
video which pays that cost per frame.

### Video CLI

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

### Device support

**CUDA or CPU only. MPS does not work**, despite being available on Apple
Silicon. The MHR backend inside `mhr_model.pt` is a TorchScript module that
computes skeleton state in float64, and MPS has no float64 support; because it
is compiled, it cannot be patched. `resolve_device()` therefore never picks MPS
automatically, and warns if you force it.

Upstream also hardcodes CUDA — three `recursive_to(batch, "cuda")` calls and
five bare `.cuda()` calls, all unconditional — so stock SAM 3D Body cannot run
on a Mac at all. `body_extractor._redirect_upstream_cuda()` shims both routes
onto the chosen device.

Measured: **~72 s per image on CPU** (M-series, includes model load).

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
cd api && python -m uvicorn main:app --reload --host 0.0.0.0
```

`--host 0.0.0.0` is needed for a physical iPhone to reach your Mac over LAN.

#### iOS path: photo in, USDZ out

```
POST /api/reconstruct        (multipart: image)
  -> { id, model_url, keypoints_url, preview_url, model: {...}, subject: {...} }
GET  <model_url>             -> model/vnd.usdz+zip
```

This runs **inline** — one image is one forward pass, so there is no job to
poll. Set a generous client timeout: seconds on MPS, longer on CPU.

The response carries URLs rather than model bytes, because RealityKit loads
from a local file. Download, write to disk, then hand over that file URL:

```swift
struct Reconstruction: Codable {
    let id: String
    let modelURL: String
    enum CodingKeys: String, CodingKey { case id, modelURL = "model_url" }
}

// 1. upload the photo
var request = URLRequest(url: URL(string: "\(baseURL)/api/reconstruct")!)
request.httpMethod = "POST"
request.timeoutInterval = 300          // inference is not instant
// ... attach `image` as multipart/form-data ...
let (data, _) = try await URLSession.shared.data(for: request)
let result = try JSONDecoder().decode(Reconstruction.self, from: data)

// 2. download the USDZ to a real file
let (tmp, _) = try await URLSession.shared.download(
    from: URL(string: "\(baseURL)\(result.modelURL)")!)
let local = FileManager.default.temporaryDirectory
    .appendingPathComponent("\(result.id).usdz")
try? FileManager.default.removeItem(at: local)
try FileManager.default.moveItem(at: tmp, to: local)

// 3. render it
RealityView { content in
    let entity = try await ModelEntity(contentsOf: local)
    content.add(entity)
}
```

The USDZ declares **Y-up** and **metres**, with a bound `UsdPreviewSurface` and
smooth vertex normals, so it shades correctly without client-side work.

If you only need a skeleton, skip the mesh: `keypoints_url` returns the 70
joints as JSON — measured at **7.3 KB against 540 KB** for the model.

Measured end-to-end: `POST /api/reconstruct` took **72 s** on CPU including the
one-time model load, returning a 0.54 MB USDZ.

#### Raw buffers instead of USDZ

If you would rather build the mesh yourself with RealityKit's `MeshDescriptor`
or `LowLevelMesh`, post `raw=true` and the response gains a `raw` block with
buffer URLs. Buffers are flat little-endian, ready to memcpy into a vertex
buffer — no parsing.

```
GET /api/topology                          uint32-le triangle indices  432 KB
GET /api/reconstruct/{id}/positions.bin    float32-le xyz              216 KB
GET /api/reconstruct/{id}/normals.bin      float32-le xyz              216 KB
```

**Fetch `/api/topology` once and cache it.** MHR's connectivity is identical for
every reconstruction, so it is served `immutable` with a strong ETag. That is
the entire reason the raw path can beat USDZ — measured on a real body:

| payload | size |
|---|---|
| USDZ | 524 KB |
| raw, first request (positions + normals + indices) | 864 KB — *worse* |
| raw, topology cached (positions + normals) | 432 KB |
| raw, positions only (normals on device) | **216 KB** — 2.4x smaller |
| positions as JSON | 1,145 KB — don't |

So raw only wins if you cache topology. For a single photo over LAN, USDZ is
simpler and the difference is marginal; raw matters for sequences, where you
stream positions per frame against topology you already hold.

```swift
var descriptor = MeshDescriptor(name: "body")
descriptor.positions = MeshBuffer(positions)      // [SIMD3<Float>]
descriptor.normals   = MeshBuffer(normals)        // [SIMD3<Float>]
descriptor.primitives = .triangles(indices)       // [UInt32], from /api/topology
let mesh = try MeshResource.generate(from: [descriptor])
content.add(ModelEntity(mesh: mesh, materials: [SimpleMaterial()]))
```

Decoding a buffer is a straight reinterpret:

```swift
let positions: [SIMD3<Float>] = data.withUnsafeBytes {
    Array($0.bindMemory(to: SIMD3<Float>.self))
}
```

For per-frame updates use `LowLevelMesh` (iOS 18+) instead — `MeshResource`
re-runs the mesh optimiser on every rebuild, which you do not want in a loop.

#### Full endpoint list

| Endpoint | Purpose |
|---|---|
| `POST /api/reconstruct` | Photo -> USDZ (the iOS path) |
| `GET /api/reconstruct/{id}/model.usdz` | Download the model |
| `GET /api/reconstruct/{id}/keypoints` | Joints as JSON |
| `GET /api/reconstruct/{id}/preview.jpg` | 2D keypoint overlay |
| `GET /api/reconstruct/{id}/positions.bin` | float32-le vertex positions (raw=true) |
| `GET /api/reconstruct/{id}/normals.bin` | float32-le vertex normals (raw=true) |
| `GET /api/topology` | uint32-le triangle indices, shared + cacheable |
| `GET /api/reconstructions` | List stored reconstructions |
| `DELETE /api/reconstruct/{id}` | Delete one |
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
| `vertices` | `(18439, 3)` MHR mesh, `(36874, 3)` faces — opt-in via `include_vertices` |
| `cam_t`, `focal_length` | Estimated camera parameters |
| `shape_params`, `body_pose_params`, `hand_pose_params` | Interpretable MHR parameters |

SAM 3D Body returns **70 keypoints** (upstream calls this layout `mhr70`), not
the 127 joints of the full MHR skeleton. Joint count and ordering come from the
loaded asset and are **not** hardcoded anywhere in this repo. To work out which indices matter for a given skill, render
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
│   ├── metrics.py          # Geometry/kinematics helpers
│   ├── mesh.py             # OBJ / PLY export
│   ├── usdz.py             # USDZ export for Apple RealityKit
│   └── reconstruct.py      # Photo -> renderable bundle
├── main.py                 # Photo -> 3D model entry point
├── api/main.py             # FastAPI server
├── config/skills.yaml      # Catalog metadata only
├── scripts/demo.py         # Extraction CLI
├── models/checkpoints/     # SAM 3D Body weights (gitignored)
└── data/
    ├── reference/
    │   ├── pictures_for_model/   # Input photos for main.py
    │   └── *.mp4                 # Reference videos
    ├── output/meshes/      # Generated 3D models (gitignored)
    └── sequences/          # Extracted video output (gitignored)
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
- `main.py` (photo CLI) and `api/main.py` (FastAPI) share a module name, so a
  bare `import main` is ambiguous depending on `sys.path` order.
- The iOS client still calls the removed `/api/analyze*` endpoints, and
  `Models.swift` is shaped to the deleted scoring layer.

## Credits

- 3D body model: [SAM 3D Body](https://github.com/facebookresearch/sam-3d-body) /
  [MHR](https://github.com/facebookresearch/MHR), Meta Superintelligence Labs
