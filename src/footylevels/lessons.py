"""Lesson metadata boundary for the API: consent, enqueue, listing, deletion and reports.

Identity always comes from a verified token. A lesson is private to its owner and hidden as soon
as its project is deleting. Enqueue is idempotent per ``Idempotency-Key`` and repeats every
precondition inside one transaction: consent, ready prepared media, clip length, no active job,
and the per-user daily quota.
"""
from datetime import timedelta
import hashlib
import json
import re

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .model_requests import LESSON_PROFILE, lesson_input_hash
from .records import (LESSON_AI_CONSENT, JobRecord, LessonRecord, LessonReport, ProjectRecord, UserRecord,
                      utc_now)
from .repositories import FirestoreRecords, RecordConflict, RecordNotFound, RecordUnavailable, reconstruction_source


class ConsentRequired(Exception):
    pass


class LessonTooLong(Exception):
    pass


class LessonQuotaExceeded(Exception):
    pass


def lesson_job_id(uid: str, key: str) -> str:
    return hashlib.sha256(json.dumps([uid, "buildLesson", key], separators=(",", ":")).encode()).hexdigest()


def admit(user, project, input_revision, existing, used, accepting, *, job_id, model, max_seconds, daily_limit):
    """Decide one create-lesson request from records read in a single transaction.

    Returns ``existing`` for an idempotent repeat, or the new queued job. Raises for every refusal.
    """
    if user.account_state != "active" or project.owner_id != user.id:
        raise RecordConflict("Account is unavailable")
    if not user.lesson_ai_consented:
        raise ConsentRequired()
    media = project.prepared_media
    if project.state != "ready" or media is None or project.input_revision != input_revision:
        raise RecordConflict("Wait for prepared media or refresh the project")
    source = reconstruction_source(project)
    input_hash = lesson_input_hash(source, model)
    if existing is not None:
        if (existing.kind, existing.project_id, existing.input_hash) != ("buildLesson", project.id, input_hash):
            raise RecordConflict("Idempotency key was reused with different input")
        return existing
    if media.duration > max_seconds + 0.05:
        raise LessonTooLong()
    if not accepting:
        raise RecordUnavailable()
    if project.active_job_id:
        raise RecordConflict("Another job is active for this video")
    if used >= daily_limit:
        raise LessonQuotaExceeded()
    return JobRecord(id=job_id, owner_id=user.id, project_id=project.id, kind="buildLesson",
                     input_revision=input_revision, input_hash=input_hash, profile_version=LESSON_PROFILE,
                     source_asset=source, deadline_at=utc_now() + timedelta(hours=24))


class FirestoreLessons(FirestoreRecords):
    def __init__(self, client, *, model: str, max_seconds: float = 20, daily_limit: int = 3):
        super().__init__(client)
        if not 1 <= max_seconds <= 60 or not 1 <= daily_limit <= 50:
            raise ValueError("Lesson limits out of range")
        self.model, self.max_seconds, self.daily_limit = model, max_seconds, daily_limit

    # --- consent -------------------------------------------------------------------------------
    def set_consent(self, uid: str, accepted: bool) -> UserRecord:
        ref = self._ref("users", uid)

        @firestore.transactional
        def change(transaction):
            user = self._record(ref.get(transaction=transaction), UserRecord)
            if user.account_state != "active":
                raise RecordConflict("Account is unavailable")
            now = utc_now()
            updated = user.model_copy(update={
                "lesson_ai_consent_at": now if accepted else None,
                "lesson_ai_consent_version": LESSON_AI_CONSENT if accepted else None, "updated_at": now})
            transaction.set(ref, updated.document())
            return updated
        return self._transact(change)

    # --- enqueue -------------------------------------------------------------------------------
    def enqueue_lesson(self, uid: str, project_id: str, input_revision: int, key: str) -> JobRecord:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", key or ""):
            raise ValueError("Invalid Idempotency-Key")
        job_ref = self._ref("jobs", lesson_job_id(uid, key))
        project_ref, user_ref = self._ref("projects", project_id), self._ref("users", uid)
        day = utc_now().strftime("%Y%m%d")
        quota_ref = self._ref("lessonQuota", f"{uid}-{day}")

        @firestore.transactional
        def enqueue(transaction):
            user = self._record(user_ref.get(transaction=transaction), UserRecord)
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), uid)
            snapshot = job_ref.get(transaction=transaction)
            quota = quota_ref.get(transaction=transaction)
            control = self._ref("runtimeControls", "reconstruction").get(transaction=transaction)
            existing = self._owned(self._record(snapshot, JobRecord), uid) if snapshot.exists else None
            used = (quota.to_dict() or {}).get("count", 0) if quota.exists else 0
            accepting = not (control.exists and control.to_dict().get("acceptingRequests") is False)
            record = admit(user, project, input_revision, existing, used, accepting, job_id=job_ref.id,
                           model=self.model, max_seconds=self.max_seconds, daily_limit=self.daily_limit)
            if record is existing:
                return existing
            transaction.create(job_ref, record.document())
            transaction.update(project_ref, {"activeJobId": record.id, "updatedAt": record.created_at})
            transaction.set(quota_ref, {"ownerId": uid, "day": day, "count": used + 1, "updatedAt": record.created_at,
                                        "expiresAt": record.created_at + timedelta(days=2)})
            return record
        return self._transact(enqueue)

    # --- reading -------------------------------------------------------------------------------
    def _visible(self, uid, lesson: LessonRecord) -> LessonRecord:
        self._owned(lesson, uid)
        if lesson.state != "ready":
            raise RecordNotFound()
        self.get_project(uid, lesson.project_id)                 # hidden once its project is deleting
        return lesson

    def get_lesson(self, uid: str, lesson_id: str) -> LessonRecord:
        return self._visible(uid, self._record(self._ref("lessons", lesson_id).get(), LessonRecord))

    def list_lessons(self, uid: str, limit: int = 50) -> list[LessonRecord]:
        self._ref("users", uid)                                  # validates the id
        query = (self.client.collection("lessons").where(filter=FieldFilter("ownerId", "==", uid))
                 .where(filter=FieldFilter("state", "==", "ready")).limit(limit))
        lessons = [self._record(snapshot, LessonRecord) for snapshot in query.stream()]
        projects = {}
        out = []
        for lesson in lessons:
            if lesson.project_id not in projects:
                try:
                    projects[lesson.project_id] = self.get_project(uid, lesson.project_id)
                except RecordNotFound:
                    projects[lesson.project_id] = None
            if projects[lesson.project_id] is not None:
                out.append(lesson)
        return sorted(out, key=lambda lesson: lesson.completed_at, reverse=True)

    # --- changes -------------------------------------------------------------------------------
    def delete_lesson(self, uid: str, lesson_id: str) -> LessonRecord:
        """Tombstone: the lesson disappears now; its bytes are reclaimed by output reconciliation.

        A second delete finds nothing (404), as for any record the owner can no longer see.
        """
        ref = self._ref("lessons", lesson_id)

        @firestore.transactional
        def delete(transaction):
            lesson = self._owned(self._record(ref.get(transaction=transaction), LessonRecord), uid)
            project_ref = self._ref("projects", lesson.project_id)
            project = self._record(project_ref.get(transaction=transaction), ProjectRecord)
            now = utc_now()
            deleted = lesson.model_copy(update={"state": "deleted", "updated_at": now})
            transaction.set(ref, deleted.document())
            if project.owner_id == uid and project.latest_lesson_id == lesson.id:
                transaction.update(project_ref, {"latestLessonId": None, "updatedAt": now})
            return deleted
        return self._transact(delete)

    def report(self, uid: str, lesson_id: str, reason: str, note: str | None) -> LessonReport:
        lesson = self.get_lesson(uid, lesson_id)
        identifier = hashlib.sha256(json.dumps([uid, lesson_id, reason], separators=(",", ":")).encode()).hexdigest()
        ref = self._ref("lessonReports", identifier)
        record = LessonReport(id=identifier, owner_id=uid, lesson_id=lesson.id, project_id=lesson.project_id,
                              reason=reason, note=note)

        @firestore.transactional
        def create(transaction):
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:                                  # one report per lesson and reason
                return self._record(snapshot, LessonReport)
            transaction.create(ref, record.document())
            return record
        return self._transact(create)
