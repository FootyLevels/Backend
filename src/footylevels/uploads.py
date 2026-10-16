"""Durable upload reservations, owner checks and atomic source publication."""
from datetime import timedelta
import hashlib
import json
import re
import random
import time

from google.api_core import exceptions

from google.cloud import firestore

from .assets import FILE_LIMIT, StorageUnavailable
from .records import AssetRef, JobRecord, ProjectRecord, UploadRecord, utc_now
from .repositories import FirestoreRecords, RecordConflict, RecordNotFound


class StorageQuotaExceeded(Exception):
    pass


class FirestoreUploads(FirestoreRecords):
    # Reserve the bucket's full per-file limit while a capability can still write.
    # Headroom remains for curated reference material and future generated outputs.
    GLOBAL_LIMIT = 800 * 1024 * 1024
    USER_LIMIT = 200 * 1024 * 1024

    def _run(self, operation):
        # The SDK retries commit-time ABORTED, but not ABORTED raised during
        # transactional reads. Start a fresh transaction for those bounded retries.
        for attempt in range(4):
            try:
                return operation(self.client.transaction())
            except (exceptions.Aborted, ValueError) as error:
                if not isinstance(error, exceptions.Aborted) and not isinstance(error.__cause__, exceptions.Aborted):
                    raise
                if attempt == 3:
                    raise StorageUnavailable() from None
                time.sleep(random.uniform(0.1, 0.3) * 2**attempt)
            except (exceptions.ServiceUnavailable, exceptions.DeadlineExceeded):
                raise StorageUnavailable() from None

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
                                  asset=asset, expires_at=now + timedelta(hours=24), cleanup_after=now + timedelta(hours=72))
            transaction.create(upload_ref, record.document())
            transaction.update(project_ref, {"activeUploadId": upload_id, "updatedAt": now})
            for ref, count in zip(ledgers, used):
                transaction.set(ref, {"bytes": count + FILE_LIMIT, "updatedAt": now})
            return record
        return self._run(create)

    def get_upload(self, uid, upload_id):
        ref = self._ref("uploads", upload_id)
        initial = self._owned(self._record(ref.get(), UploadRecord), uid)
        project_ref = self._ref("projects", initial.project_id)
        @firestore.transactional
        def read(transaction):
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), uid)
            record = self._owned(self._record(ref.get(transaction=transaction), UploadRecord), uid)
            if record.status == "committed":
                if project.video != record.asset or project.input_revision != record.committed_revision:
                    raise RecordConflict("Pinned source changed")
            elif record.status == "uploading" and project.active_upload_id != upload_id:
                raise RecordConflict("Upload is no longer active")
            return record
        # A coherent snapshot avoids observing the new project with an old
        # upload record while a duplicate finalization request is committing.
        return self._run(read)

    def grant_upload(self, uid, upload_id):
        # A fixed quarantine extends 48h beyond the logical upload expiry. It
        # covers a token minted at the last moment (2h) plus a 24h TUS session.
        # Refresh requires owner/state checks but does not contend on metadata.
        record = self.get_upload(uid, upload_id)
        if record.status != "uploading" or record.expires_at <= utc_now():
            raise RecordConflict("Upload expired or is not active")
        return record

    def commit_upload(self, uid, upload_id):
        ref = self._ref("uploads", upload_id)
        ledgers = self._ledgers(uid)
        initial = self.get_upload(uid, upload_id)
        project_ref = self._ref("projects", initial.project_id)
        @firestore.transactional
        def commit(transaction):
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), uid)
            record = self._owned(self._record(ref.get(transaction=transaction), UploadRecord), uid)
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
            # Source publication and durable CPU dispatch commit together. Never
            # create a playable state from a checksum alone or use an in-memory queue.
            job_id = hashlib.sha256(json.dumps([uid, project.id, project.input_revision,
                record.asset.sha256, "media-720p30-v1"], separators=(",", ":")).encode()).hexdigest()
            job = JobRecord(id=job_id, owner_id=uid, project_id=project.id, kind="prepareMedia",
                input_revision=project.input_revision, input_hash=record.asset.sha256,
                profile_version="media-720p30-v1", source_asset=record.asset)
            project = project.model_copy(update={"active_job_id": job.id})
            transaction.create(self._ref("jobs", job.id), job.document())
            transaction.set(project_ref, project.document())
            transaction.set(ref, record.model_copy(update={"status": "committed", "committed_revision": project.input_revision,
                "reserved_bytes": record.asset.byte_size, "updated_at": now}).document())
            for ledger, count in zip(ledgers, counts):
                transaction.set(ledger, {"bytes": count - reduction, "updatedAt": now})
            return project
        return self._run(commit)

    def cancel_upload(self, uid, upload_id):
        ref = self._ref("uploads", upload_id)
        initial = self.get_upload(uid, upload_id)
        project_ref = self._ref("projects", initial.project_id)
        @firestore.transactional
        def cancel(transaction):
            project = self._owned(self._record(project_ref.get(transaction=transaction), ProjectRecord), uid)
            record = self._owned(self._record(ref.get(transaction=transaction), UploadRecord), uid)
            if record.status == "committed":
                raise RecordConflict("Delete the project to remove a committed recording")
            if record.status != "cancelled":
                transaction.set(ref, record.model_copy(update={"status": "cancelled", "updated_at": utc_now()}).document())
                if project.active_upload_id == upload_id:
                    transaction.update(project_ref, {"activeUploadId": None, "updatedAt": utc_now()})
            # Keep quota until all outstanding signed/TUS writes have expired and
            # exact cleanup is verified. Releasing now would permit late orphan writes.
            return {"id": upload_id, "status": "cancelled", "cleanupAfter": record.cleanup_after}
        return self._run(cancel)

    def cleanup_expired(self, upload_id, storage):
        """Trusted maintenance entrypoint; no client can bypass capability quarantine."""
        ref = self._ref("uploads", upload_id)
        initial = self._record(ref.get(), UploadRecord)
        project_ref = self._ref("projects", initial.project_id)
        @firestore.transactional
        def fence(transaction):
            project_snapshot = project_ref.get(transaction=transaction)
            record = self._record(ref.get(transaction=transaction), UploadRecord)
            now = utc_now()
            if record.status == "committed" or record.cleanup_after > now or record.expires_at > now:
                raise RecordConflict("Outstanding capability or committed source cannot be removed")
            project = self._record(project_snapshot, ProjectRecord) if project_snapshot.exists else None
            if project and project.owner_id != record.owner_id:
                raise RecordConflict("Upload owner mismatch")
            if record.status != "cancelled":
                record = record.model_copy(update={"status": "cancelled", "updated_at": now})
                transaction.set(ref, record.document())
            if project and project.active_upload_id == record.id:
                transaction.update(project_ref, {"activeUploadId": None, "updatedAt": now})
            return record
        record = self._run(fence)
        if record.reserved_bytes == 0:
            return record
        storage.delete_exact(record.asset)
        storage.verify_absent(record.asset)
        ledgers = self._ledgers(record.owner_id)
        @firestore.transactional
        def release(transaction):
            current = self._record(ref.get(transaction=transaction), UploadRecord)
            if current.reserved_bytes == 0:
                return current
            if current.status != "cancelled" or current.asset != record.asset:
                raise RecordConflict("Upload changed during cleanup")
            snapshots = [ledger.get(transaction=transaction) for ledger in ledgers]
            counts = [(s.to_dict() or {}).get("bytes", 0) for s in snapshots]
            if any(n < current.reserved_bytes for n in counts):
                raise RecordConflict("Storage accounting requires reconciliation")
            for ledger, count in zip(ledgers, counts):
                transaction.set(ledger, {"bytes": count - current.reserved_bytes, "updatedAt": utc_now()})
            updated = current.model_copy(update={"reserved_bytes": 0, "updated_at": utc_now()})
            transaction.set(ref, updated.document())
            return updated
        return self._run(release)

    def reserve_artifact(self, uid, project_id, asset):
        """Trusted runner budget, before immutable output upload; no HTTP write route.

        FP-39 must fence the job/attempt before calling this and FP-18/54 must
        verify exact object cleanup before releasing a stale reservation.
        """
        prefix = f"users/{uid}/projects/{project_id}/reconstruction/"
        if asset.bucket != "private-media" or not asset.object_key.startswith(prefix):
            raise RecordConflict("Generated asset outside project prefix")
        identifier = hashlib.sha256(asset.object_key.encode()).hexdigest()
        ref = self._ref("assetReservations", identifier)
        ledgers = self._ledgers(uid)
        @firestore.transactional
        def reserve(transaction):
            self._owned(self._record(self._ref("projects", project_id).get(transaction=transaction), ProjectRecord), uid)
            previous = ref.get(transaction=transaction)
            if previous.exists:
                raw = previous.to_dict()
                if (raw["ownerId"], raw["projectId"], raw["asset"]) != (uid, project_id, asset.document()):
                    raise RecordConflict("Artifact reservation is immutable")
                return identifier
            snapshots = [ledger.get(transaction=transaction) for ledger in ledgers]
            counts = [(s.to_dict() or {}).get("bytes", 0) for s in snapshots]
            if counts[0] + asset.byte_size > self.GLOBAL_LIMIT or counts[1] + asset.byte_size > self.USER_LIMIT:
                raise StorageQuotaExceeded()
            transaction.create(ref, {"ownerId": uid, "projectId": project_id, "asset": asset.document(),
                                     "bytes": asset.byte_size, "createdAt": utc_now()})
            for ledger, count in zip(ledgers, counts):
                transaction.set(ledger, {"bytes": count + asset.byte_size, "updatedAt": utc_now()})
            return identifier
        return self._run(reserve)
