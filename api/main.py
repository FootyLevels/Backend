"""
FootyLevels API
FastAPI backend for soccer skill analysis.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import tempfile
import shutil
from pathlib import Path
import uuid
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from footylevels import SkillAnalyzer, generate_feedback

app = FastAPI(
    title="FootyLevels API",
    description="AI-powered soccer skill analysis",
    version="0.1.0"
)

# CORS middleware for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store for analysis jobs (in production, use Redis/database)
analysis_jobs = {}


class AnalysisRequest(BaseModel):
    """Request to analyze a skill."""
    skill: str
    reference_video_url: Optional[str] = None


class AnalysisResponse(BaseModel):
    """Response from skill analysis."""
    skill_name: str
    overall_score: float
    overall_rating: str
    summary: str
    strengths: List[dict]
    improvements: List[dict]
    tips: List[dict]
    encouragement: str


class SkillInfo(BaseModel):
    """Information about a supported skill."""
    id: str
    name: str
    description: str
    difficulty: str


# Initialize analyzer (lazy loading)
_analyzer = None

def get_analyzer():
    global _analyzer
    if _analyzer is None:
        _analyzer = SkillAnalyzer()
    return _analyzer


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "FootyLevels API"}


@app.get("/api/skills", response_model=List[SkillInfo])
async def list_skills():
    """List all supported skills."""
    analyzer = get_analyzer()
    skills = []
    
    for skill_id, config in analyzer.skills_config.get('skills', {}).items():
        skills.append(SkillInfo(
            id=skill_id,
            name=config.get('name', skill_id),
            description=config.get('description', ''),
            difficulty=config.get('difficulty', 'intermediate')
        ))
    
    return skills


@app.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str):
    """Get details for a specific skill."""
    analyzer = get_analyzer()
    
    try:
        config = analyzer.get_skill_config(skill_id)
        return {
            "id": skill_id,
            "name": config.get('name'),
            "description": config.get('description'),
            "difficulty": config.get('difficulty'),
            "phases": config.get('phases', []),
            "checkpoints": [
                {
                    "id": cp['id'],
                    "name": cp['name'],
                    "description": cp.get('description', '')
                }
                for cp in config.get('checkpoints', [])
            ]
        }
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")


@app.post("/api/analyze")
async def analyze_skill(
    skill: str,
    reference_video: UploadFile = File(...),
    user_video: UploadFile = File(...)
):
    """
    Analyze a user's skill attempt against a reference.
    
    - **skill**: Skill ID (e.g., 'cruyff_turn')
    - **reference_video**: Video of the skill performed correctly
    - **user_video**: Video of the user's attempt
    """
    analyzer = get_analyzer()
    
    # Validate skill
    try:
        analyzer.get_skill_config(skill)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown skill: {skill}")
    
    # Save uploaded files temporarily
    temp_dir = Path(tempfile.mkdtemp())
    
    try:
        ref_path = temp_dir / f"reference_{uuid.uuid4().hex}.mp4"
        user_path = temp_dir / f"user_{uuid.uuid4().hex}.mp4"
        
        # Save reference video
        with open(ref_path, "wb") as f:
            shutil.copyfileobj(reference_video.file, f)
        
        # Save user video
        with open(user_path, "wb") as f:
            shutil.copyfileobj(user_video.file, f)
        
        # Run analysis
        result = analyzer.analyze_skill(skill, str(ref_path), str(user_path))
        
        # Generate feedback
        feedback = generate_feedback(result)
        
        return {
            "success": True,
            "analysis": result.to_dict(),
            "feedback": feedback.to_dict()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        # Cleanup temp files
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/analyze/quick")
async def quick_analyze(
    skill: str,
    user_video: UploadFile = File(...)
):
    """
    Quick analysis using a pre-stored reference video.
    
    For MVP, reference videos are stored on the server.
    """
    analyzer = get_analyzer()
    
    # Check for pre-stored reference
    ref_path = Path(__file__).parent.parent / "data" / "reference" / f"{skill}.mp4"
    
    if not ref_path.exists():
        raise HTTPException(
            status_code=400, 
            detail=f"No reference video available for skill: {skill}"
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
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/analyze/webcam")
async def analyze_from_webcam_frames(
    skill: str,
    frames: List[str]  # Base64 encoded frames
):
    """
    Analyze from webcam frames (for real-time web app).
    
    This endpoint accepts base64-encoded frames instead of video files.
    Useful for browser-based recording.
    """
    # This would process frames directly without saving to video
    # Implementation depends on frontend requirements
    raise HTTPException(status_code=501, detail="Not implemented yet")


# Error handlers
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": str(exc),
            "detail": "An unexpected error occurred"
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
