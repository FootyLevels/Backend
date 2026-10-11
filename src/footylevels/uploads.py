"""Durable upload reservations, owner checks and atomic source publication."""
from datetime import timedelta
import hashlib
import json
import re

from google.cloud import firestore

from .assets import FILE_LIMIT
from .records import AssetRef, ProjectRecord, UploadRecord, utc_now
from .repositories import FirestoreRecords, RecordConflict, RecordNotFound


class StorageQuotaExceeded(Exception):
    pass


class FirestoreUploads(FirestoreRecords):
    # Reserve the bucket's full per-file limit while a capability can still write.
    # Headroom remains for curated reference material and future generated outputs.
    GLOBAL_LIMIT = 800 * 1024 * 1024
    USER_LIMIT = 200 * 1024 * 1024

    def _ledgers(self, uid):
        return self._ref("storageUsage", "global"), self._ref("storageUsage", hashlib.sha256(uid.encode()).hexdigest())

    def begin(self, uid, project_id, expected_revision, size, checksum, content_type, key):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", key):
            raise ValueError("An opaque Idempotency-Key of 16–80 characters is required")
        upload_id = hashlib.sha256(json.dumps([uid, project_id, key]).encode()).hexdigest()
        extension = {"video/mp4": "mp4", "video/quicktime": "mov"}.get(content_type)
        if not extension:
            raise ValueError("Only MP4 and QuickTime recordings are accepted")
        asset = AssetRef(bucket="private-media", object_key=f"users/{uid}/projects/{project_id}/source/{upload_id}.{extension}",
                         byte_size=size, sha256=checksum, content_type=content_type)
        project_ref, upload_ref = self._ref("projects", project_id), self._ref("uploads", upload_id)
        ledgers = self._ledgers(uid)

        @firestore.transactional
        def create(transaction):
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), uid)
            existing = upload_ref.get(transaction=transaction)
            if existing.exists:
                record = self._record(existing, UploadRecord)
                if record.asset != asset or record.expected_revision != expected_revision:
                    raise RecordConflict("Idempotency key was reused with different input")
                return record
            if project.input_revision != expected_revision or project.active_job_id or project.active_upload_id:
                raise RecordConflict("Input changed or an operation is active")
            # Source uploads are immutable: use a new project for a new clip.
            if project.video is not None or project.state != "uploading":
                raise RecordConflict("A source recording is already pinned")
            snapshots = [ref.get(transaction=transaction) for ref in ledgers]
            used = [(snapshot.to_dict() or {}).get("bytes", 0) for snapshot in snapshots]
            if used[0] + FILE_LIMIT > self.GLOBAL_LIMIT or used[1] + FILE_LIMIT > self.USER_LIMIT:
                raise StorageQuotaExceeded()
            now = utc_now()
            record = UploadRecord(id=upload_id, owner_id=uid, project_id=project_id, expected_revision=expected_revision,
                                  asset=asset, expires_at=now + timedelta(hours=24), cleanup_after=now + timedelta(hours=48))
            transaction.create(upload_ref, record.document())
            transaction.update(project_ref, {"activeUploadId": upload_id, "updatedAt": now})
            for ref, count in zip(ledgers, used):
                transaction.set(ref, {"bytes": count + FILE_LIMIT, "updatedAt": now})
            return record
        return create(self.client.transaction())

    def get_upload(self, uid, upload_id):
        record = self._owned(self._record(self._ref("uploads", upload_id).get(), UploadRecord), uid)
        project = self.get_project(uid, record.project_id)
        if record.status == "committed":
            if project.video != record.asset or project.input_revision != record.committed_revision:
                raise RecordConflict("Pinned source changed")
        elif record.status == "uploading" and project.active_upload_id != upload_id:
            raise RecordConflict("Upload is no longer active")
        return record

    def grant_upload(self, uid, upload_id):
        # Fences refresh against deletion/finalization and tracks late capability writes.
        ref = self._ref("uploads", upload_id)
        @firestore.transactional
        def grant(transaction):
            record = self._owned(self._record(ref.get(transaction=transaction), UploadRecord), uid)
            project = self._owned(self._record(self._ref("projects", record.project_id).get(transaction=transaction), ProjectRecord), uid)
            now = utc_now()
            if record.status != "uploading" or record.expires_at <= now or project.active_upload_id != upload_id:
                raise RecordConflict("Upload expired or is not active")
            updated = record.model_copy(update={"cleanup_after": now + timedelta(hours=48), "updated_at": now})
            transaction.set(ref, updated.document())
            return updated
        return grant(self.client.transaction())

    def commit_upload(self, uid, upload_id):
        ref = self._ref("uploads", upload_id)
        ledgers = self._ledgers(uid)
        @firestore.transactional
        def commit(transaction):
            record = self._owned(self._record(ref.get(transaction=transaction), UploadRecord), uid)
            project_ref = self._ref("projects", record.project_id)
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), uid)
            if record.status == "committed":
                if project.video != record.asset or project.input_revision != record.committed_revision:
                    raise RecordConflict("Pinned source changed")
                return project
            now = utc_now()
            if (record.status != "uploading" or record.expires_at <= now or project.active_upload_id != upload_id
                    or project.input_revision != record.expected_revision or project.video is not None or project.active_job_id):
                raise RecordConflict("Upload finalization is stale")
            snapshots = [ledger.get(transaction=transaction) for ledger in ledgers]
            counts = [(s.to_dict() or {}).get("bytes", 0) for s in snapshots]
            reduction = record.reserved_bytes - record.asset.byte_size
            if any(n < record.reserved_bytes for n in counts):
                raise RecordConflict("Storage accounting requires reconciliation")
            project = ProjectRecord.model_validate({**project.document(), "video": record.asset.document(),
                "inputRevision": project.input_revision + 1, "state": "preparing", "activeUploadId": None, "updatedAt": now})
            # Valid bytes are not yet a playable video. FP-54 owns decoding/preparation.
            transaction.set(project_ref, project.document())
            transaction.set(ref, record.model_copy(update={"status": "committed", "committed_revision": project.input_revision,
                "reserved_bytes": record.asset.byte_size, "updated_at": now}).document())
            for ledger, count in zip(ledgers, counts):
                transaction.set(ledger, {"bytes": count - reduction, "updatedAt": now})
            return project
        return commit(self.client.transaction())

    def cancel_upload(self, uid, upload_id):
        ref = self._ref("uploads", upload_id)
        @firestore.transactional
        def cancel(transaction):
            record = self._owned(self._record(ref.get(transaction=transaction), UploadRecord), uid)
            project_ref = self._ref("projects", record.project_id)
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), uid)
            if record.status == "committed":
                raise RecordConflict("Delete the project to remove a committed recording")
            if record.status != "cancelled":
                transaction.set(ref, record.model_copy(update={"status": "cancelled", "updated_at": utc_now()}).document())
                if project.active_upload_id == upload_id:
                    transaction.update(project_ref, {"activeUploadId": None, "updatedAt": utc_now()})
            # Keep quota until all outstanding signed/TUS writes have expired and
            # exact cleanup is verified. Releasing now would permit late orphan writes.
            return {"id": upload_id, "status": "cancelled", "cleanupAfter": record.cleanup_after}
        return cancel(self.client.transaction())
