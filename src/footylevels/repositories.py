"""Application metadata boundary. Callers supply identity from verified Firebase tokens.

No HTTP routes, model loading or blob access is implemented here. FP-14 connects
this boundary to authenticated requests; FP-53 implements runner claims/recovery.
"""
import base64
from datetime import datetime
import hashlib
import json
from typing import Protocol

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import TypeAdapter

from .records import (AssetRef, ComparisonRecord, Id, JobRecord, ProjectRecord,
                      ReconstructionRecord, ReferenceRecord, UserRecord, utc_now)


class RecordNotFound(Exception):
    """Missing, deleted, or inaccessible; do not disclose another owner's record."""


class RecordConflict(Exception):
    """Stale input, incompatible state or rejected late publication."""


class RecordsRepository(Protocol):
    def get_user(self, uid: str) -> UserRecord: ...
    def bootstrap_user(self, uid: str, display_name: str) -> UserRecord: ...
    def create_project(self, uid: str, title: str, source: str) -> ProjectRecord: ...
    def get_project(self, uid: str, project_id: str) -> ProjectRecord: ...
    def list_projects(self, uid: str, limit: int = 20, cursor: str | None = None) -> tuple[list[ProjectRecord], str | None]: ...
    def enqueue_reconstruction(self, uid: str, project_id: str, input_revision: int,
                               kind: str, input_hash: str, profile_version: str) -> JobRecord: ...
    def publish_reconstruction(self, result: ReconstructionRecord, lease_token: str) -> ReconstructionRecord: ...
    def rename_project(self, uid: str, project_id: str, title: str) -> ProjectRecord: ...
    def set_source(self, uid: str, project_id: str, source: AssetRef, expected_revision: int) -> ProjectRecord: ...
    def mark_deleting(self, uid: str, project_id: str) -> ProjectRecord: ...
    def get_job(self, uid: str, job_id: str) -> JobRecord: ...
    def get_reconstruction(self, uid: str, reconstruction_id: str) -> ReconstructionRecord: ...
    def create_reference(self, reference: ReferenceRecord) -> ReferenceRecord: ...
    def get_reference(self, reference_id: str, version: str) -> ReferenceRecord: ...
    def create_comparison(self, record: ComparisonRecord, uid: str) -> ComparisonRecord: ...
    def get_comparison(self, uid: str, comparison_id: str) -> ComparisonRecord: ...


_id = TypeAdapter(Id)
VISIBLE_STATES = ["uploading", "preparing", "ready", "rejected"]
ACTIVE_JOBS = {"queued", "running", "retrying"}


class FirestoreRecords:
    def __init__(self, client: firestore.Client):
        self.client = client

    def _ref(self, collection: str, identifier: str):
        return self.client.collection(collection).document(_id.validate_python(identifier))

    @staticmethod
    def _record(snapshot, model):
        if not snapshot.exists:
            raise RecordNotFound()
        record = model.model_validate(snapshot.to_dict())
        if record.id != snapshot.id:
            raise RecordConflict("Record ID does not match its document")
        return record

    @staticmethod
    def _owned(record, uid):
        _id.validate_python(uid)
        if record.owner_id != uid or getattr(record, "state", None) in {"deleting", "deleted"} or getattr(record, "deleted_at", None):
            raise RecordNotFound()
        return record

    def bootstrap_user(self, uid: str, display_name: str) -> UserRecord:
        record = UserRecord(id=uid, display_name=display_name)
        ref = self._ref("users", uid)

        @firestore.transactional
        def create(transaction):
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                existing = self._record(snapshot, UserRecord)
                if existing.account_state != "active":
                    raise RecordConflict("Account is unavailable")
                return existing
            transaction.create(ref, record.document())
            return record

        return create(self.client.transaction())

    def get_user(self, uid: str) -> UserRecord:
        return self._record(self._ref("users", uid).get(), UserRecord)

    def create_project(self, uid: str, title: str, source: str) -> ProjectRecord:
        # The service invokes this only after verifying Firebase identity/account access.
        ref = self.client.collection("projects").document()
        record = ProjectRecord(id=ref.id, owner_id=uid, title=title, source=source)
        ref.create(record.document())
        return record

    def get_project(self, uid: str, project_id: str) -> ProjectRecord:
        return self._owned(self._record(self._ref("projects", project_id).get(), ProjectRecord), uid)

    def list_projects(self, uid: str, limit: int = 20, cursor: str | None = None):
        _id.validate_python(uid)
        if not 1 <= limit <= 50:
            raise ValueError("Page limit must be 1–50")
        query = (self.client.collection("projects")
                 .where(filter=FieldFilter("ownerId", "==", uid))
                 .where(filter=FieldFilter("state", "in", VISIBLE_STATES))
                 .order_by("createdAt", direction=firestore.Query.DESCENDING)
                 .order_by("__name__", direction=firestore.Query.ASCENDING))
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError()
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                created_at = datetime.fromisoformat(decoded["createdAt"])
                if decoded["ownerId"] != uid or created_at.tzinfo is None:
                    raise ValueError()
                query = query.start_after({"createdAt": created_at, "__name__": self._ref("projects", decoded["id"])})
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                raise ValueError("Invalid library cursor") from error
        snapshots = list(query.limit(limit + 1).stream())
        items = [self._owned(self._record(item, ProjectRecord), uid) for item in snapshots[:limit]]
        next_cursor = None
        if len(snapshots) > limit:
            last = items[-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps({"ownerId": uid, "id": last.id,
                "createdAt": last.created_at.isoformat()}, separators=(",", ":")).encode()).decode()
        return items, next_cursor

    def rename_project(self, uid: str, project_id: str, title: str) -> ProjectRecord:
        ref = self._ref("projects", project_id)

        @firestore.transactional
        def rename(transaction):
            existing = self._owned(self._record(ref.get(transaction=transaction), ProjectRecord), uid)
            record = ProjectRecord.model_validate({**existing.document(), "title": title, "updatedAt": utc_now()})
            transaction.set(ref, record.document())
            return record

        return rename(self.client.transaction())

    def set_source(self, uid: str, project_id: str, source: AssetRef, expected_revision: int) -> ProjectRecord:
        ref = self._ref("projects", project_id)

        @firestore.transactional
        def change(transaction):
            existing = self._owned(self._record(ref.get(transaction=transaction), ProjectRecord), uid)
            if existing.input_revision != expected_revision or existing.active_job_id or existing.active_upload_id:
                raise RecordConflict("Input changed or a job is active")
            prefix = f"users/{uid}/projects/{project_id}/source/"
            if source.bucket != "private-media" or not source.object_key.startswith(prefix):
                raise RecordConflict("Source asset does not belong to this project")
            record = ProjectRecord.model_validate({**existing.document(), "video": source.document(),
                "inputRevision": existing.input_revision + 1, "state": "ready", "updatedAt": utc_now(),
                "latestReconstructionId": None})
            transaction.set(ref, record.document())
            return record

        return change(self.client.transaction())

    def mark_deleting(self, uid: str, project_id: str) -> ProjectRecord:
        ref = self._ref("projects", project_id)

        @firestore.transactional
        def delete(transaction):
            existing = self._record(ref.get(transaction=transaction), ProjectRecord)
            if existing.owner_id != uid:
                raise RecordNotFound()
            if existing.state in {"deleting", "deleted"}:
                return existing
            job_ref = self._ref("jobs", existing.active_job_id) if existing.active_job_id else None
            job = self._record(job_ref.get(transaction=transaction), JobRecord) if job_ref else None
            if job and (job.owner_id != uid or job.project_id != project_id):
                raise RecordConflict("Active job does not belong to the project")
            now = utc_now()
            record = ProjectRecord.model_validate({**existing.document(), "state": "deleting", "deletedAt": now,
                                                  "updatedAt": now, "activeJobId": None, "activeUploadId": None})
            transaction.set(ref, record.document())
            if job and job.status in ACTIVE_JOBS:
                transaction.update(job_ref, {"status": "cancelled", "finishedAt": now, "updatedAt": now})
            return record

        return delete(self.client.transaction())

    def enqueue_reconstruction(self, uid: str, project_id: str, input_revision: int,
                               kind: str, input_hash: str, profile_version: str) -> JobRecord:
        # Semantic deduplication survives a restart and concurrent duplicate requests.
        key = json.dumps([uid, project_id, input_revision, kind, input_hash, profile_version], separators=(",", ":"))
        job_id = hashlib.sha256(key.encode()).hexdigest()
        project_ref, job_ref = self._ref("projects", project_id), self._ref("jobs", job_id)

        @firestore.transactional
        def enqueue(transaction):
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), uid)
            snapshot = job_ref.get(transaction=transaction)
            if project.input_revision != input_revision or project.state != "ready" or project.video is None:
                raise RecordConflict("Reconstruction input is stale or unavailable")
            if snapshot.exists:
                return self._owned(self._record(snapshot, JobRecord), uid)
            if project.active_job_id:
                raise RecordConflict("Another reconstruction is active")
            record = JobRecord(id=job_id, owner_id=uid, project_id=project_id, input_revision=input_revision,
                               input_hash=input_hash, kind=kind, profile_version=profile_version, source_asset=project.video)
            transaction.create(job_ref, record.document())
            transaction.update(project_ref, {"activeJobId": job_id, "updatedAt": record.created_at})
            return record

        return enqueue(self.client.transaction())

    def get_job(self, uid: str, job_id: str) -> JobRecord:
        job = self._owned(self._record(self._ref("jobs", job_id).get(), JobRecord), uid)
        self.get_project(uid, job.project_id)
        return job

    def publish_reconstruction(self, result: ReconstructionRecord, lease_token: str) -> ReconstructionRecord:
        # Results use the job ID as their immutable ID; retries cannot create new winners.
        if result.id != result.job_id:
            raise RecordConflict("Result ID must match job ID")
        job_ref = self._ref("jobs", result.job_id)
        project_ref = self._ref("projects", result.project_id)
        result_ref = self._ref("reconstructions", result.id)

        @firestore.transactional
        def publish(transaction):
            job = self._record(job_ref.get(transaction=transaction), JobRecord)
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), result.owner_id)
            snapshot = result_ref.get(transaction=transaction)
            if (job.owner_id, job.project_id, job.input_revision, job.source_asset, job.profile_version) != (result.owner_id, result.project_id, result.input_revision, result.source_asset, result.profile_version):
                raise RecordConflict("Result provenance does not match the job")
            if project.input_revision != result.input_revision or project.video != result.source_asset:
                raise RecordConflict("Result input revision is obsolete")
            if snapshot.exists:
                existing = self._record(snapshot, ReconstructionRecord)
                if existing != result or job.reconstruction_id != result.id:
                    raise RecordConflict("Published output is immutable")
                return existing
            now = utc_now()
            if (job.status != "running" or job.attempt_count < 1 or not lease_token or job.lease_token != lease_token
                    or job.lease_expires_at is None or job.lease_expires_at <= now
                    or project.active_job_id != job.id):
                raise RecordConflict("Worker lease is stale or cancelled")
            expected_mode = "motion" if job.kind == "reconstructMotion" else "keyframes"
            prefix = f"users/{result.owner_id}/projects/{project.id}/reconstruction/{job.id}/{job.attempt_count}/"
            if result.mode != expected_mode or any(asset.bucket != "private-media" or not asset.object_key.startswith(prefix) for asset in result.artifacts):
                raise RecordConflict("Result mode or attempt asset paths do not match the job")
            transaction.create(result_ref, result.document())
            transaction.update(job_ref, {"status": "succeeded" if result.outcome == "ready" else "partial",
                                        "reconstructionId": result.id, "finishedAt": now, "updatedAt": now})
            transaction.update(project_ref, {"latestReconstructionId": result.id, "activeJobId": None, "updatedAt": now})
            return result

        return publish(self.client.transaction())

    def get_reconstruction(self, uid: str, reconstruction_id: str) -> ReconstructionRecord:
        record = self._owned(self._record(self._ref("reconstructions", reconstruction_id).get(), ReconstructionRecord), uid)
        self.get_project(uid, record.project_id)
        return record

    def create_reference(self, reference: ReferenceRecord) -> ReferenceRecord:
        # Trusted curator entrypoint; FP-14 must never expose this as a player write.
        self._ref("references", reference.id).create(reference.document())
        return reference

    def get_reference(self, reference_id: str, version: str) -> ReferenceRecord:
        record = self._record(self._ref("references", reference_id).get(), ReferenceRecord)
        if record.publication_state != "published" or record.version != version:
            raise RecordNotFound()
        return record

    def _comparison_access(self, record: ComparisonRecord, uid: str):
        self._owned(record, uid)
        self.get_reconstruction(uid, record.left_reconstruction_id)
        if record.right_type == "reconstruction":
            self.get_reconstruction(uid, record.right_id)
        else:
            self.get_reference(record.right_id, record.right_reference_version)
        return record

    def create_comparison(self, record: ComparisonRecord, uid: str) -> ComparisonRecord:
        self._comparison_access(record, uid)
        self._ref("comparisons", record.id).create(record.document())
        return record

    def get_comparison(self, uid: str, comparison_id: str) -> ComparisonRecord:
        record = self._record(self._ref("comparisons", comparison_id).get(), ComparisonRecord)
        return self._comparison_access(record, uid)
