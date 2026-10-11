"""Durable CPU job claims/publication, sharing existing Firestore ownership/quota rules."""
from datetime import timedelta
import hashlib
import json
import secrets

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .records import JobRecord, PreparedMedia, ProjectRecord, utc_now
from .repositories import ACTIVE_JOBS, RecordConflict, RecordNotFound
from .uploads import FirestoreUploads, StorageQuotaExceeded


class FirestoreMediaJobs(FirestoreUploads):
    LEASE_SECONDS = 900

    def candidates(self, limit=20, after=None):
        # One existing single-field project index; no hot polling across all jobs.
        # The CLI holds a local process lock and uses a rotating document cursor.
        query = self.client.collection("projects").where(filter=FieldFilter("state", "==", "preparing")).order_by("__name__")
        if after:
            query = query.start_after({"__name__": self._ref("projects", after)})
        return [snapshot.id for snapshot in query.limit(limit).stream()]

    def claim(self, project_id, worker_id):
        project_ref = self._ref("projects", project_id)
        @firestore.transactional
        def claim(transaction):
            project = self._record(project_ref.get(transaction=transaction), ProjectRecord)
            if project.state != "preparing" or not project.video or project.deleted_at:
                return None
            job_id = hashlib.sha256(json.dumps([project.owner_id, project.id, project.input_revision,
                project.video.sha256, "media-720p30-v1"], separators=(",", ":")).encode()).hexdigest()
            if project.active_job_id and project.active_job_id != job_id:
                raise RecordConflict("A different job is active")
            job_ref = self._ref("jobs", job_id)
            snapshot = job_ref.get(transaction=transaction)
            job = self._record(snapshot, JobRecord) if snapshot.exists else JobRecord(
                id=job_id, owner_id=project.owner_id, project_id=project.id, kind="prepareMedia",
                input_revision=project.input_revision, input_hash=project.video.sha256,
                source_asset=project.video, profile_version="media-720p30-v1")
            self._provenance(project, job)
            now = utc_now()
            if job.status not in ACTIVE_JOBS:
                return None
            if job.status == "running" and job.lease_expires_at and job.lease_expires_at > now:
                return None
            if job.attempt_count >= job.max_attempts:
                transaction.set(job_ref, job.model_copy(update={"status": "failed", "stage": "failed",
                    "error_code": "preparation_unavailable", "finished_at": now, "updated_at": now,
                    "lease_token": None, "lease_expires_at": None}).document())
                transaction.update(project_ref, {"state": "rejected", "mediaError": "preparation_unavailable",
                                                 "activeJobId": None, "updatedAt": now})
                return None
            claimed = job.model_copy(update={"status": "running", "stage": "preparingMedia",
                "attempt_count": job.attempt_count + 1, "lease_token": secrets.token_hex(24),
                "lease_expires_at": now + timedelta(seconds=self.LEASE_SECONDS), "worker_id": worker_id,
                "error_code": None, "updated_at": now})
            transaction.set(job_ref, claimed.document())
            transaction.update(project_ref, {"activeJobId": claimed.id, "updatedAt": now})
            return claimed
        return self._run(claim)

    @staticmethod
    def _provenance(project, job):
        if (job.kind != "prepareMedia" or job.profile_version != "media-720p30-v1"
                or (project.owner_id, project.id, project.input_revision, project.video) !=
                   (job.owner_id, job.project_id, job.input_revision, job.source_asset)):
            raise RecordConflict("Media input changed")

    def _current(self, transaction, job):
        project = self._owned(self._record(self._ref("projects", job.project_id).get(transaction=transaction), ProjectRecord), job.owner_id)
        current = self._record(self._ref("jobs", job.id).get(transaction=transaction), JobRecord)
        self._provenance(project, current)
        if (current.status != "running" or not job.lease_token or current.lease_token != job.lease_token
                or current.attempt_count != job.attempt_count or current.lease_expires_at is None
                or current.lease_expires_at <= utc_now() or project.state != "preparing" or project.active_job_id != job.id):
            raise RecordConflict("Media worker lease is stale")
        return project, current

    def checkpoint(self, job, stage):
        if stage not in {"downloadingMedia", "decodingMedia", "publishingMedia"}:
            raise ValueError("Unknown media stage")
        @firestore.transactional
        def checkpoint(transaction):
            self._current(transaction, job)
            transaction.update(self._ref("jobs", job.id), {"stage": stage, "updatedAt": utc_now()})
        return self._run(checkpoint)

    def reserve_outputs(self, job, assets):
        prefix = f"users/{job.owner_id}/projects/{job.project_id}/media/{job.id}/{job.attempt_count}/"
        if (not 1 <= len(assets) <= 7 or len({a.object_key for a in assets}) != len(assets)
                or any(a.bucket != "private-media" or not a.object_key.startswith(prefix) for a in assets)):
            raise RecordConflict("Media outputs must belong to this attempt")
        reservations = [(self._ref("assetReservations", hashlib.sha256(a.object_key.encode()).hexdigest()), a) for a in assets]
        ledgers = self._ledgers(job.owner_id)
        @firestore.transactional
        def reserve(transaction):
            self._current(transaction, job)
            previous = [ref.get(transaction=transaction) for ref, asset in reservations]
            counts = [(ref.get(transaction=transaction).to_dict() or {}).get("bytes", 0) for ref in ledgers]
            additional = 0
            for snapshot, (_, asset) in zip(previous, reservations):
                if snapshot.exists:
                    raw = snapshot.to_dict()
                    if raw["asset"] != asset.document() or raw.get("jobId") != job.id or raw.get("attempt") != job.attempt_count:
                        raise RecordConflict("Reserved output is immutable")
                else:
                    additional += asset.byte_size
            if counts[0] + additional > self.GLOBAL_LIMIT or counts[1] + additional > self.USER_LIMIT:
                raise StorageQuotaExceeded()
            now = utc_now()
            for snapshot, (ref, asset) in zip(previous, reservations):
                if not snapshot.exists:
                    transaction.create(ref, {"ownerId": job.owner_id, "projectId": job.project_id, "jobId": job.id,
                        "attempt": job.attempt_count, "asset": asset.document(), "bytes": asset.byte_size, "createdAt": now})
            for ledger, count in zip(ledgers, counts):
                transaction.set(ledger, {"bytes": count + additional, "updatedAt": now})
            return assets
        return self._run(reserve)

    def publish(self, job, media: PreparedMedia):
        @firestore.transactional
        def publish(transaction):
            project, current = self._current(transaction, job)
            prefix = f"users/{job.owner_id}/projects/{job.project_id}/media/{job.id}/{job.attempt_count}/"
            assets = [media.video, *[p.asset for p in media.previews]]
            for asset in assets:
                if not asset.object_key.startswith(prefix):
                    raise RecordConflict("Prepared media belongs to a different attempt")
                snapshot = self._ref("assetReservations", hashlib.sha256(asset.object_key.encode()).hexdigest()).get(transaction=transaction)
                if not snapshot.exists or snapshot.to_dict()["asset"] != asset.document():
                    raise RecordConflict("Prepared media was not budgeted")
            now = utc_now()
            result = project.model_copy(update={"prepared_media": media, "state": "ready", "media_error": None,
                                                "active_job_id": None, "updated_at": now})
            transaction.set(self._ref("projects", project.id), result.document())
            transaction.set(self._ref("jobs", job.id), current.model_copy(update={"status": "succeeded", "stage": "ready",
                "finished_at": now, "lease_token": None, "lease_expires_at": None, "updated_at": now}).document())
            return result
        return self._run(publish)

    def fail(self, job, code, *, retry=False):
        @firestore.transactional
        def fail(transaction):
            project, current = self._current(transaction, job)
            now = utc_now()
            status = "retrying" if retry and current.attempt_count < current.max_attempts else "failed"
            failed = JobRecord.model_validate({**current.document(), "status": status, "stage": status,
                "errorCode": code, "updatedAt": now, "finishedAt": None if status == "retrying" else now,
                "leaseToken": None, "leaseExpiresAt": None})
            transaction.set(self._ref("jobs", job.id), failed.document())
            if status == "failed":
                transaction.update(self._ref("projects", project.id), {"state": "rejected", "mediaError": code,
                                                                       "activeJobId": None, "updatedAt": now})
            return failed
        return self._run(fail)

    def cleanup_orphan_output(self, identifier, storage):
        """Trusted reconciliation after a 24h writer grace; never a player route."""
        from .records import AssetRef
        ref = self._ref("assetReservations", identifier)
        @firestore.transactional
        def fence(transaction):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                raise RecordNotFound()
            raw = snapshot.to_dict()
            if "jobId" not in raw or not raw.get("bytes") or raw["createdAt"] > utc_now() - timedelta(hours=24):
                raise RecordConflict("Output is not eligible for reconciliation")
            asset = AssetRef.model_validate(raw["asset"])
            job = self._record(self._ref("jobs", raw["jobId"]).get(transaction=transaction), JobRecord)
            project = self._record(self._ref("projects", raw["projectId"]).get(transaction=transaction), ProjectRecord)
            prefix = f"users/{raw['ownerId']}/projects/{project.id}/media/{job.id}/{raw['attempt']}/"
            if (job.kind != "prepareMedia" or job.owner_id != raw["ownerId"] or job.project_id != project.id
                    or project.owner_id != raw["ownerId"] or not asset.object_key.startswith(prefix)):
                raise RecordConflict("Output provenance mismatch")
            published = [] if not project.prepared_media else [project.prepared_media.video,
                                                              *[p.asset for p in project.prepared_media.previews]]
            if asset in published or (job.status == "running" and job.attempt_count == raw["attempt"]
                                      and job.lease_expires_at and job.lease_expires_at > utc_now()):
                raise RecordConflict("Output is published or an active worker owns it")
            # Fence the old attempt before deleting bytes, so it cannot reserve
            # or publish again. A later attempt uses different immutable paths.
            if job.attempt_count == raw["attempt"] and job.status == "running":
                transaction.update(self._ref("jobs", job.id), {"status": "retrying", "leaseToken": None,
                                                               "leaseExpiresAt": None, "updatedAt": utc_now()})
            return raw, asset
        raw, asset = self._run(fence)
        storage.delete_exact(asset)
        storage.verify_absent(asset)
        ledgers = self._ledgers(raw["ownerId"])
        @firestore.transactional
        def release(transaction):
            current = ref.get(transaction=transaction).to_dict()
            if not current or not current.get("bytes"):
                return
            if current["asset"] != raw["asset"]:
                raise RecordConflict("Reservation changed during reconciliation")
            counts = [(ledger.get(transaction=transaction).to_dict() or {}).get("bytes", 0) for ledger in ledgers]
            if any(count < current["bytes"] for count in counts):
                raise RecordConflict("Storage accounting requires reconciliation")
            now = utc_now()
            for ledger, count in zip(ledgers, counts):
                transaction.set(ledger, {"bytes": count - current["bytes"], "updatedAt": now})
            transaction.update(ref, {"bytes": 0, "cleanedAt": now})
        self._run(release)
