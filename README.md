# FootyLevels ⚽

AI-powered soccer skill analysis. Compare your technique against professional players and get actionable feedback.

## Quick Start

```bash
# Clone and setup
cd footylevels
pip install -r requirements.txt

# Run tests
python scripts/demo.py --test

# Test with webcam
python scripts/demo.py --webcam

# Analyze videos
python scripts/demo.py --reference ref.mp4 --user my_attempt.mp4 --skill cruyff_turn

# Start API server
source .venv/bin/activate  # Activate venv if not already active
cd api
python -m uvicorn main:app --reload
```

## Features

- 🎯 **Pose Extraction**: Uses MediaPipe for accurate body tracking
- 🔄 **Video Alignment**: DTW algorithm handles different video lengths/speeds
- 📊 **Metric Calculation**: Joint angles, rotations, velocities
- 💬 **Actionable Feedback**: Human-readable tips and scores
- 🌐 **REST API**: Ready for mobile/web integration

## Supported Skills

| Skill | Difficulty | Key Checkpoints |
|-------|------------|-----------------|
| Cruyff Turn | Intermediate | Hip rotation, plant foot, shoulder fake |
| Step Over | Beginner | Foot arc, body lean, move speed |
| Elastico | Advanced | Ankle rotation, ball control, fluidity |

## Architecture

```
User Video → Pose Extraction → Alignment → Metrics → Feedback
                    ↓              ↓          ↓
              MediaPipe         DTW      Checkpoints
                    ↓              ↓          ↓
              33 landmarks    Frame map   Scores + Tips
```

## API Usage

```bash
# Start the API server (with venv activated)
cd api
python -m uvicorn main:app --reload

# In another terminal, test the API
# List available skills
curl http://localhost:8000/api/skills

# Quick analyze (uses pre-stored reference video)
curl -X POST http://localhost:8000/api/analyze/quick \
  -F "skill=cruyff_turn" \
  -F "user_video=@my_attempt.mp4"

# Full analyze (with custom reference video)
curl -X POST http://localhost:8000/api/analyze \
  -F "skill=cruyff_turn" \
  -F "reference_video=@reference.mp4" \
  -F "user_video=@my_attempt.mp4"
```

## Example Output

```json
{
  "skill_name": "Cruyff Turn",
  "overall_score": 72.5,
  "overall_rating": "good",
  "summary": "Good Cruyff Turn! You've got the basics down with room for refinement.",
  "improvements": [
    {
      "title": "Hip Rotation",
      "detail": "Rotate your hips more fully - aim for a complete 180° turn",
      "current_value": 135.2,
      "target_value": 180.0
    }
  ],
  "tips": [
    {
      "title": "Practice Tip",
      "detail": "Practice the move slowly first, focusing on the drag behind your standing leg"
    }
  ]
}
```

## Project Structure

```
footylevels/
├── src/footylevels/
│   ├── pose_extractor.py   # MediaPipe pose extraction
│   ├── alignment.py        # DTW sequence alignment
│   ├── metrics.py          # Angle/distance calculations
│   ├── analyzer.py         # Main orchestration
│   └── feedback.py         # Feedback generation
├── api/
│   └── main.py             # FastAPI server
├── config/
│   └── skills.yaml         # Skill definitions
├── scripts/
│   └── demo.py             # Demo/test script
└── data/
    ├── reference/          # Pro reference videos
    └── samples/            # Test videos
```

## Adding New Skills

Edit `config/skills.yaml`:

```yaml
skills:
  your_skill:
    name: "Your Skill"
    description: "Description"
    checkpoints:
      - id: checkpoint_1
        name: "Checkpoint Name"
        metric_type: rotation_angle
        ideal_value: 90
        feedback:
          good: "Great job!"
          poor: "Work on this..."
```

## Installation

### From source

```bash
# Clone the repository
git clone https://github.com/yourusername/footylevels.git
cd footylevels

# Create virtual environment (Python 3.11 recommended for MediaPipe compatibility)
python3.11 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Or install as package
pip install -e .
```

### Requirements

- Python 3.8+
- OpenCV
- MediaPipe
- NumPy
- FastAPI (for API)

## Development

```bash
# Activate virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run specific tests
pytest tests/test_metrics.py

# Format code
black src/

# Type check
mypy src/
```

## Next Steps

- [ ] Add more soccer skills
- [ ] Ball tracking integration
- [ ] Real-time analysis mode
- [ ] Mobile app (React Native)
- [ ] Training progress tracking

## Credits

- Pose estimation: [MediaPipe](https://mediapipe.dev/)
- Inspired by: [BeONE Sports](https://beonesports.com), [PoseSync](https://github.com/infocusp/posesync)

---

Built with ❤️ for soccer players everywhere.
