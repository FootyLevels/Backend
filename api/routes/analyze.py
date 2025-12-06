"""
Analysis Routes
API endpoints for skill analysis.
"""

from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from fastapi.responses import FileResponse
from typing import Optional
import tempfile
import shutil
from pathlib import Path
import uuid
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from footylevels import SkillAnalyzer, generate_feedback

router = APIRouter(prefix="/api", tags=["analysis"])

# Shared analyzer instance
_analyzer = None


def get_analyzer():
    """Get or create analyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = SkillAnalyzer()
    return _analyzer


@router.post("/analyze/video")
async def analyze_from_videos(
    skill: str = Form(...),
    reference_video: UploadFile = File(...),
    user_video: UploadFile = File(...)
):
    """
    Analyze skill from uploaded videos.

    Args:
        skill: Skill name (e.g., 'cruyff_turn')
        reference_video: Reference video file
        user_video: User attempt video file

    Returns:
        Analysis results with feedback
    """
    analyzer = get_analyzer()

    # Validate skill exists
    try:
        analyzer.get_skill_config(skill)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown skill: {skill}")

    # Create temp directory for processing
    temp_dir = Path(tempfile.mkdtemp())

    try:
        # Save uploaded files
        ref_path = temp_dir / f"ref_{uuid.uuid4().hex}.mp4"
        user_path = temp_dir / f"user_{uuid.uuid4().hex}.mp4"

        with open(ref_path, "wb") as f:
            shutil.copyfileobj(reference_video.file, f)

        with open(user_path, "wb") as f:
            shutil.copyfileobj(user_video.file, f)

        # Run analysis
        result = analyzer.analyze_skill(skill, str(ref_path), str(user_path))
        feedback = generate_feedback(result)

        return {
            "success": True,
            "analysis": result.to_dict(),
            "feedback": feedback.to_dict()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)


@router.post("/analyze/quick")
async def quick_analyze(
    skill: str = Form(...),
    user_video: UploadFile = File(...)
):
    """
    Quick analysis using pre-stored reference video.

    Args:
        skill: Skill name
        user_video: User's video attempt

    Returns:
        Analysis results
    """
    analyzer = get_analyzer()

    # Validate skill
    try:
        analyzer.get_skill_config(skill)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown skill: {skill}")

    # Check for reference video
    ref_path = Path(__file__).parent.parent.parent / "data" / "reference" / f"{skill}.mp4"

    if not ref_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No reference video found for skill: {skill}"
        )

    temp_dir = Path(tempfile.mkdtemp())

    try:
        user_path = temp_dir / f"user_{uuid.uuid4().hex}.mp4"

        with open(user_path, "wb") as f:
            shutil.copyfileobj(user_video.file, f)

        result = analyzer.analyze_skill(skill, str(ref_path), str(user_path))
        feedback = generate_feedback(result)

        return {
            "success": True,
            "analysis": result.to_dict(),
            "feedback": feedback.to_dict()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@router.get("/reference/{skill_id}")
async def get_reference_video(skill_id: str):
    """
    Download reference video for a skill.

    Args:
        skill_id: Skill identifier

    Returns:
        Video file
    """
    ref_path = Path(__file__).parent.parent.parent / "data" / "reference" / f"{skill_id}.mp4"

    if not ref_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Reference video not found for skill: {skill_id}"
        )

    return FileResponse(
        str(ref_path),
        media_type="video/mp4",
        filename=f"{skill_id}_reference.mp4"
    )
