"""Durable reconstruction lifecycle; model execution is supplied by the FP-39 adapter."""
from datetime import timedelta
import secrets

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .records import JobRecord, ProjectRecord, utc_now
from .repositories import ACTIVE_JOBS, RecordConflict, reconstruction_source
from .media_jobs import FirestoreMediaJobs


class FirestoreReconstructionJobs(FirestoreMediaJobs):
    LEASE_SECONDS = 300
    ATTEMPT_SECONDS = 3600
    OUTPUT_LIMIT = 32
    OUTPUT_DIRECTORY = "reconstruction"
    STAGES = {"downloadingInput": 0, "generatingBodies": 1, "packingAssets": 2, "publishingAssets": 3}
    KINDS = frozenset({"reconstructKeyframes", "reconstructMotion"})
    FAIL_CODES = frozenset({"model_unavailable", "reconstruction_failed", "job_deadline"})
    EXHAUSTED_CODE = "reconstruction_failed"

    def candidates(self, limit=10, after=None):
        if not 1 <= limit <= 50:
            raise ValueError("Candidate limit must be 1–50")
        # One existing single-field index, including expired running jobs. The
        # cursor names a document and stays valid after completion or deletion.
        query = (self.client.collection("jobs").where(filter=FieldFilter("status", "in", sorted(ACTIVE_JOBS)))
                 .order_by("__name__"))
        if after:
            query = query.start_after({"__name__": self._ref("jobs", after)})
        return list(query.limit(limit).stream())

    @staticmethod
    def _matches(project, job):
        return (project.owner_id == job.owner_id and project.id == job.project_id
                and project.state == "ready" and not project.deleted_at
                and project.active_job_id == job.id and project.input_revision == job.input_revision
                and reconstruction_source(project) == job.source_asset)

    def _finish(self, transaction, project, job, code):
        now = utc_now()
        status = "cancelled" if code == "input_changed" else "failed"
        terminal = job.model_copy(update={"status": status, "stage": status, "error_code": code,
            "updated_at": now, "finished_at": now, "lease_token": None, "lease_expires_at": None,
            "next_attempt_at": None})
        transaction.set(self._ref("jobs", job.id), terminal.document())
        if project and project.owner_id == job.owner_id and project.active_job_id == job.id:
            transaction.update(self._ref("projects", project.id), {"activeJobId": None, "updatedAt": now})
        return terminal

    def claim(self, job_id, worker_id):
        @firestore.transactional
        def claim(transaction):
            snapshot = self._ref("jobs", job_id).get(transaction=transaction)
            if not snapshot.exists:
                return None
            job = self._record(snapshot, JobRecord)
            if job.kind not in self.KINDS or job.status not in ACTIVE_JOBS:
                return None
            project_snapshot = self._ref("projects", job.project_id).get(transaction=transaction)
            project = self._record(project_snapshot, ProjectRecord) if project_snapshot.exists else None
            now = utc_now()
            if not project or not self._matches(project, job):
                self._finish(transaction, project, job, "input_changed")
                return None
            deadline = job.deadline_at or job.created_at + timedelta(hours=24)
            if deadline <= now:
                self._finish(transaction, project, job, "job_deadline")
                return None
            if job.status == "running" and job.lease_expires_at and job.lease_expires_at > now:
                if not job.attempt_deadline_at or job.attempt_deadline_at > now:
                    return None
            if job.next_attempt_at and job.next_attempt_at > now:
                return None
            if job.attempt_count >= job.max_attempts:
                self._finish(transaction, project, job, self.EXHAUSTED_CODE)
                return None
            claimed = job.model_copy(update={"status": "running", "stage": "downloadingInput",
                "attempt_count": job.attempt_count + 1, "lease_token": secrets.token_hex(24),
                "lease_expires_at": now + timedelta(seconds=self.LEASE_SECONDS), "worker_id": worker_id,
                "heartbeat_at": now, "attempt_deadline_at": min(deadline, now + timedelta(seconds=self.ATTEMPT_SECONDS)),
                "deadline_at": deadline, "next_attempt_at": None, "error_code": None, "updated_at": now})
            transaction.set(self._ref("jobs", job.id), claimed.document())
            return claimed
        return self._run(claim)

    def _current(self, transaction, claim):
        project = self._record(self._ref("projects", claim.project_id).get(transaction=transaction), ProjectRecord)
        current = self._record(self._ref("jobs", claim.id).get(transaction=transaction), JobRecord)
        now = utc_now()
        if (not self._matches(project, current) or current.kind not in self.KINDS or current.status != "running"
                or not claim.lease_token or current.lease_token != claim.lease_token
                or current.attempt_count != claim.attempt_count or not current.lease_expires_at
                or current.lease_expires_at <= now or not current.attempt_deadline_at
                or current.attempt_deadline_at <= now or not current.deadline_at or current.deadline_at <= now):
            raise RecordConflict("Reconstruction worker is stale, cancelled or overdue")
        return project, current

    def checkpoint(self, claim, stage=None):
        if stage is not None and stage not in self.STAGES:
            raise ValueError("Unknown job stage")
        @firestore.transactional
        def heartbeat(transaction):
            _, current = self._current(transaction, claim)
            if stage is not None and self.STAGES[stage] < self.STAGES[current.stage]:
                raise RecordConflict("A checkpoint cannot move backwards")
            now = utc_now()
            updated = current.model_copy(update={"stage": stage or current.stage, "heartbeat_at": now,
                "updated_at": now, "lease_expires_at": min(current.attempt_deadline_at,
                                                            now + timedelta(seconds=self.LEASE_SECONDS))})
            transaction.set(self._ref("jobs", claim.id), updated.document())
            return updated
        return self._run(heartbeat)

    def fail(self, claim, code="model_unavailable", *, retry=True):
        if code not in self.FAIL_CODES:
            raise ValueError("Expected a safe error code")
        @firestore.transactional
        def fail(transaction):
            project, current = self._current(transaction, claim)
            now = utc_now()
            backoff = now + timedelta(seconds=120 * 2 ** (current.attempt_count - 1))
            if not retry or current.attempt_count >= current.max_attempts or backoff >= current.deadline_at:
                return self._finish(transaction, project, current, code)
            updated = current.model_copy(update={"status": "retrying", "stage": "retrying", "error_code": code,
                "next_attempt_at": backoff, "updated_at": now, "lease_token": None, "lease_expires_at": None})
            transaction.set(self._ref("jobs", claim.id), updated.document())
            return updated
        return self._run(fail)
