"""Durable lesson-building lifecycle: claims, heartbeats, fenced publication.

Shares the reconstruction lease rules (one GPU runner, immutable per-attempt output prefixes,
budgeted reservations) and differs only in kind, stages, output directory and result record.
"""
import hashlib

from google.cloud import firestore

from .records import LessonRecord, utc_now
from .reconstruction_jobs import FirestoreReconstructionJobs
from .repositories import RecordConflict, reconstruction_source


class FirestoreLessonJobs(FirestoreReconstructionJobs):
    ATTEMPT_SECONDS = 3600
    OUTPUT_LIMIT = 64
    OUTPUT_DIRECTORY = "lesson"
    STAGES = {"downloadingInput": 0, "screeningVideo": 1, "generatingBodies": 2, "writingLesson": 3,
              "checkingLesson": 4, "publishingAssets": 5}
    KINDS = frozenset({"buildLesson"})
    FAIL_CODES = frozenset({"model_unavailable", "reconstruction_failed", "job_deadline",
                            "lesson_unavailable", "lesson_rejected"})
    EXHAUSTED_CODE = "lesson_unavailable"

    def publish_lesson(self, claim, lesson: LessonRecord) -> LessonRecord:
        """Create the lesson once, from the live lease of this exact attempt."""
        if lesson.id != claim.id or lesson.job_id != claim.id:
            raise RecordConflict("Lesson ID must match its job")
        lesson_ref = self._ref("lessons", lesson.id)

        @firestore.transactional
        def publish(transaction):
            project, current = self._current(transaction, claim)
            snapshot = lesson_ref.get(transaction=transaction)
            if ((current.owner_id, current.project_id, current.input_revision, current.source_asset)
                    != (lesson.owner_id, lesson.project_id, lesson.input_revision, lesson.source_asset)
                    or reconstruction_source(project) != lesson.source_asset):
                raise RecordConflict("Lesson provenance does not match the job")
            if snapshot.exists:
                existing = self._record(snapshot, LessonRecord)
                if existing != lesson:
                    raise RecordConflict("Published lesson is immutable")
                return existing
            prefix = f"users/{lesson.owner_id}/projects/{project.id}/lesson/{claim.id}/{current.attempt_count}/"
            for asset in lesson.artifacts:
                if not asset.object_key.startswith(prefix):
                    raise RecordConflict("Lesson files belong to a different attempt")
                reservation = self._reservation(asset).get(transaction=transaction)
                if not reservation.exists or reservation.to_dict()["asset"] != asset.document():
                    raise RecordConflict("Lesson files were not budgeted")
            now = utc_now()
            transaction.create(lesson_ref, lesson.document())
            transaction.update(self._ref("jobs", claim.id), {
                "status": "succeeded", "stage": "ready", "lessonId": lesson.id, "finishedAt": now, "updatedAt": now,
                "leaseToken": None, "leaseExpiresAt": None, "nextAttemptAt": None, "errorCode": None})
            transaction.update(self._ref("projects", project.id), {
                "latestLessonId": lesson.id, "activeJobId": None, "updatedAt": now})
            return lesson
        return self._run(publish)

    def _reservation(self, asset):
        return self._ref("assetReservations", hashlib.sha256(asset.object_key.encode()).hexdigest())

