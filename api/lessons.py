"""Lessons from a user's own prepared video: consent, create, read, assets, delete, report."""
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from footylevels.lessons import ConsentRequired, LessonQuotaExceeded, LessonTooLong
from footylevels.records import Id, Metadata, utc_now
from footylevels.repositories import RecordConflict

MAX_ARTIFACTS = 64


class LessonConsent(Metadata):
    accepted: bool
    version: Literal["lesson-ai-v1"] = "lesson-ai-v1"


class CreateLesson(Metadata):
    input_revision: int = Field(ge=1, strict=True)


class ReportLesson(Metadata):
    reason: Literal["wrong_move", "wrong_timing", "unsafe", "inaccurate", "other"]
    note: str | None = Field(default=None, max_length=500)


def public_lesson(lesson):
    return lesson.model_dump(mode="json", by_alias=True, exclude={"artifacts", "source_asset"})


def bundle_paths(lesson):
    """Bundle-relative name → asset, from one attempt of the lesson's own job prefix only."""
    prefix = f"users/{lesson.owner_id}/projects/{lesson.project_id}/lesson/{lesson.job_id}/"
    mapping, attempt = {}, None
    for asset in lesson.artifacts:
        if asset.bucket != "private-media" or not asset.object_key.startswith(prefix):
            raise RecordConflict("Artifact outside lesson prefix")
        number, separator, name = asset.object_key[len(prefix):].partition("/")
        if (not separator or not number.isdecimal() or int(number) < 1 or not name or name in mapping
                or any(part in {"", ".", ".."} for part in name.split("/")) or name.count("/") > 1):
            raise RecordConflict("Ambiguous artifact paths")
        if attempt is not None and attempt != number:
            raise RecordConflict("Artifacts cross output attempts")
        attempt = number
        mapping[name] = asset
    return mapping


def lesson_router(records, lessons, assets, active_identity, public_job):
    router = APIRouter()

    @router.put("/api/me/consents/lesson-ai")
    def consent(body: LessonConsent, user=Depends(active_identity)):
        updated = lessons.set_consent(user.uid, body.accepted)
        return {"lessonAi": {"accepted": updated.lesson_ai_consented, "version": updated.lesson_ai_consent_version,
                             "acceptedAt": updated.lesson_ai_consent_at}}

    @router.post("/api/projects/{project_id}/lessons", status_code=202)
    def create(project_id: Id, body: CreateLesson, response: Response,
               idempotency_key: Annotated[str, Header(pattern=r"^[a-zA-Z0-9_-]{16,80}$")],
               user=Depends(active_identity)):
        try:
            job = lessons.enqueue_lesson(user.uid, project_id, body.input_revision, idempotency_key)
        except ConsentRequired:
            return JSONResponse({"detail": "Allow AI lesson creation first", "code": "consent_required"}, status_code=403)
        except LessonTooLong:
            raise HTTPException(422, f"Lessons are made from clips up to {lessons.max_seconds:g} seconds") from None
        except LessonQuotaExceeded:
            return JSONResponse({"detail": "Daily lesson limit reached; try again tomorrow", "code": "lesson_quota"},
                                status_code=429, headers={"Retry-After": "3600"})
        response.headers["Location"] = f"/api/jobs/{job.id}"
        return public_job(job)

    @router.get("/api/lessons")
    def listing(user=Depends(active_identity)):
        return {"items": [public_lesson(lesson) for lesson in lessons.list_lessons(user.uid)]}

    @router.get("/api/lessons/{lesson_id}")
    def lesson(lesson_id: Id, user=Depends(active_identity)):
        return public_lesson(lessons.get_lesson(user.uid, lesson_id))

    @router.post("/api/lessons/{lesson_id}/assets")
    def lesson_assets(lesson_id: Id, user=Depends(active_identity)):
        if assets is None:
            raise HTTPException(503, "Private storage is not configured")
        record = lessons.get_lesson(user.uid, lesson_id)
        mapping = bundle_paths(record)
        if len(mapping) > MAX_ARTIFACTS:
            raise RecordConflict("Too many artifacts")
        # Check the entire set before minting any capability, as for reconstructions.
        signed = {name: {"url": assets.signed_read(asset), "sha256": asset.sha256,
                         "byteSize": asset.byte_size, "contentType": asset.content_type} for name, asset in mapping.items()}
        if lessons.get_lesson(user.uid, lesson_id) != record:
            raise RecordConflict()
        return {"expiresAt": utc_now() + timedelta(seconds=300), "assets": signed}

    @router.delete("/api/lessons/{lesson_id}", status_code=202)
    def delete(lesson_id: Id, user=Depends(active_identity)):
        record = lessons.delete_lesson(user.uid, lesson_id)
        return {"id": record.id, "state": record.state}

    @router.post("/api/lessons/{lesson_id}/reports", status_code=201)
    def report(lesson_id: Id, body: ReportLesson, user=Depends(active_identity)):
        record = lessons.report(user.uid, lesson_id, body.reason, body.note)
        return {"id": record.id, "reason": record.reason}

    return router
