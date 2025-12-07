"""
FootyLevels API
FastAPI backend for soccer skill analysis.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Form
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
    player_id: Optional[str] = None
    player_name: Optional[str] = None
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    origin_story: Optional[str] = None


class PlayerInfo(BaseModel):
    """Information about a player."""
    id: str
    name: str
    nickname: str
    nationality: str
    era: str
    category: str
    image_url: str
    bio: str
    skills: List[str] = []


class CategoryInfo(BaseModel):
    """Information about a skill category."""
    id: str
    name: str
    skills: List[str]


class ProgressionInfo(BaseModel):
    """Skill progression by difficulty."""
    beginner: List[str]
    intermediate: List[str]
    advanced: List[str]


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
    """List all supported skills with player info."""
    analyzer = get_analyzer()
    skills = []
    players = analyzer.skills_config.get('players', {})

    for skill_id, config in analyzer.skills_config.get('skills', {}).items():
        player_id = config.get('player_id')
        player = players.get(player_id, {}) if player_id else {}

        skills.append(SkillInfo(
            id=skill_id,
            name=config.get('name', skill_id),
            description=config.get('description', ''),
            difficulty=config.get('difficulty', 'intermediate'),
            player_id=player_id,
            player_name=player.get('name'),
            video_url=config.get('video_url'),
            thumbnail_url=config.get('thumbnail_url'),
            origin_story=config.get('origin_story')
        ))

    return skills


@app.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str):
    """Get details for a specific skill including player info."""
    analyzer = get_analyzer()

    try:
        config = analyzer.get_skill_config(skill_id)
        players = analyzer.skills_config.get('players', {})
        player_id = config.get('player_id')
        player = players.get(player_id, {}) if player_id else {}

        return {
            "id": skill_id,
            "name": config.get('name'),
            "description": config.get('description'),
            "difficulty": config.get('difficulty'),
            "player_id": player_id,
            "player": player if player else None,
            "video_url": config.get('video_url'),
            "thumbnail_url": config.get('thumbnail_url'),
            "origin_story": config.get('origin_story'),
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


@app.get("/api/players", response_model=List[PlayerInfo])
async def list_players():
    """List all players with their associated skills."""
    analyzer = get_analyzer()
    players = []
    skills_config = analyzer.skills_config.get('skills', {})
    players_config = analyzer.skills_config.get('players', {})

    # Build player -> skills mapping
    player_skills = {}
    for skill_id, skill in skills_config.items():
        player_id = skill.get('player_id')
        if player_id:
            if player_id not in player_skills:
                player_skills[player_id] = []
            player_skills[player_id].append(skill_id)

    for player_id, config in players_config.items():
        players.append(PlayerInfo(
            id=player_id,
            name=config.get('name', player_id),
            nickname=config.get('nickname', ''),
            nationality=config.get('nationality', ''),
            era=config.get('era', ''),
            category=config.get('category', 'legend'),
            image_url=config.get('image_url', ''),
            bio=config.get('bio', ''),
            skills=player_skills.get(player_id, [])
        ))

    return players


@app.get("/api/players/{player_id}")
async def get_player(player_id: str):
    """Get details for a specific player and their skills."""
    analyzer = get_analyzer()
    players_config = analyzer.skills_config.get('players', {})
    skills_config = analyzer.skills_config.get('skills', {})

    if player_id not in players_config:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}")

    player = players_config[player_id]

    # Get skills for this player
    player_skills = []
    for skill_id, skill in skills_config.items():
        if skill.get('player_id') == player_id:
            player_skills.append({
                "id": skill_id,
                "name": skill.get('name'),
                "description": skill.get('description'),
                "difficulty": skill.get('difficulty'),
                "video_url": skill.get('video_url'),
                "thumbnail_url": skill.get('thumbnail_url'),
                "origin_story": skill.get('origin_story')
            })

    return {
        "id": player_id,
        "name": player.get('name'),
        "nickname": player.get('nickname'),
        "nationality": player.get('nationality'),
        "era": player.get('era'),
        "category": player.get('category'),
        "image_url": player.get('image_url'),
        "bio": player.get('bio'),
        "skills": player_skills
    }


@app.get("/api/players/category/{category}")
async def list_players_by_category(category: str):
    """List players by category (legend or current)."""
    analyzer = get_analyzer()
    players_config = analyzer.skills_config.get('players', {})
    skills_config = analyzer.skills_config.get('skills', {})

    # Build player -> skills mapping
    player_skills = {}
    for skill_id, skill in skills_config.items():
        player_id = skill.get('player_id')
        if player_id:
            if player_id not in player_skills:
                player_skills[player_id] = []
            player_skills[player_id].append(skill_id)

    players = []
    for player_id, config in players_config.items():
        if config.get('category') == category:
            players.append(PlayerInfo(
                id=player_id,
                name=config.get('name', player_id),
                nickname=config.get('nickname', ''),
                nationality=config.get('nationality', ''),
                era=config.get('era', ''),
                category=config.get('category', 'legend'),
                image_url=config.get('image_url', ''),
                bio=config.get('bio', ''),
                skills=player_skills.get(player_id, [])
            ))

    return players


@app.get("/api/categories", response_model=List[CategoryInfo])
async def list_categories():
    """List all skill categories."""
    analyzer = get_analyzer()
    categories = analyzer.skills_config.get('categories', [])

    return [
        CategoryInfo(
            id=cat.get('id', ''),
            name=cat.get('name', ''),
            skills=cat.get('skills', [])
        )
        for cat in categories
    ]


@app.get("/api/progression", response_model=ProgressionInfo)
async def get_progression():
    """Get skill progression by difficulty level."""
    analyzer = get_analyzer()
    progression = analyzer.skills_config.get('progression', {})

    return ProgressionInfo(
        beginner=progression.get('beginner', []),
        intermediate=progression.get('intermediate', []),
        advanced=progression.get('advanced', [])
    )


@app.get("/api/skills/difficulty/{difficulty}")
async def list_skills_by_difficulty(difficulty: str):
    """List skills filtered by difficulty level."""
    if difficulty not in ['beginner', 'intermediate', 'advanced']:
        raise HTTPException(status_code=400, detail="Invalid difficulty. Use: beginner, intermediate, advanced")

    analyzer = get_analyzer()
    skills = []
    players = analyzer.skills_config.get('players', {})

    for skill_id, config in analyzer.skills_config.get('skills', {}).items():
        if config.get('difficulty') == difficulty:
            player_id = config.get('player_id')
            player = players.get(player_id, {}) if player_id else {}

            skills.append(SkillInfo(
                id=skill_id,
                name=config.get('name', skill_id),
                description=config.get('description', ''),
                difficulty=config.get('difficulty', 'intermediate'),
                player_id=player_id,
                player_name=player.get('name'),
                video_url=config.get('video_url'),
                thumbnail_url=config.get('thumbnail_url'),
                origin_story=config.get('origin_story')
            ))

    return skills


@app.post("/api/analyze")
async def analyze_skill(
    skill: str = Form(...),
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
    skill: str = Form(...),
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
