"""Owned source upload and allowlisted private asset capabilities."""
from datetime import timedelta
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field

from footylevels.assets import StorageUnavailable
from footylevels.records import Id, Metadata, Sha256, utc_now
from footylevels.repositories import RecordConflict, RecordNotFound
from footylevels.uploads import StorageQuotaExceeded


class BeginUpload(Metadata):
    expected_revision: int = Field(ge=1)
    byte_size: int = Field(gt=0, le=52428800)
    sha256: Sha256
    content_type: Literal["video/mp4", "video/quicktime"]


def asset_router(records, uploads, assets, active_identity):
    router = APIRouter()

    @router.post("/api/projects/{project_id}/upload-session")
    def begin(project_id: Id, body: BeginUpload,
              idempotency_key: Annotated[str, Header(min_length=16, max_length=80)], user=Depends(active_identity)):
        record = uploads.begin(user.uid, project_id, body.expected_revision, body.byte_size,
                               body.sha256, body.content_type, idempotency_key)
        if record.status == "committed":
            uploads.get_upload(user.uid, record.id)
            return {"id": record.id, "status": record.status, "inputRevision": record.committed_revision}
        return capability(user.uid, record.id)

    def capability(uid, upload_id):
        record = uploads.grant_upload(uid, upload_id)
        grant = assets.signed_upload(record.asset)
        # The provider fixes signed-upload lifetime at two hours; the logical
        # upload lasts 24h and can be refreshed by its owner. This is a bearer secret.
        uploads.get_upload(uid, upload_id)
        return {"id": upload_id, "status": record.status, "expiresAt": record.expires_at,
                "capabilityExpiresAt": utc_now() + timedelta(hours=2),
                "expectedBytes": record.asset.byte_size, "maxBytes": 52428800, **grant}

    @router.get("/api/uploads/{upload_id}")
    def status(upload_id: Id, user=Depends(active_identity)):
        record = uploads.get_upload(user.uid, upload_id)
        return {"id": record.id, "status": record.status, "expectedBytes": record.asset.byte_size,
                "expiresAt": record.expires_at, "inputRevision": record.committed_revision}

    @router.post("/api/uploads/{upload_id}/refresh")
    def refresh(upload_id: Id, user=Depends(active_identity)):
        return capability(user.uid, upload_id)

    @router.post("/api/uploads/{upload_id}/finalize", status_code=202)
    def finalize(upload_id: Id, user=Depends(active_identity)):
        record = uploads.get_upload(user.uid, upload_id)
        if record.status != "committed":
            if record.status != "uploading" or record.expires_at <= utc_now():
                raise RecordConflict("Upload expired or cancelled")
            assets.verify(record.asset)
        # Atomic revision/status fencing after I/O prevents late publication.
        return uploads.commit_upload(user.uid, upload_id)

    @router.delete("/api/uploads/{upload_id}", status_code=202)
    def cancel(upload_id: Id, user=Depends(active_identity)):
        return uploads.cancel_upload(user.uid, upload_id)

    @router.post("/api/projects/{project_id}/assets")
    def source_assets(project_id: Id, user=Depends(active_identity)):
        project = records.get_project(user.uid, project_id)
        asset = project.video
        prefix = f"users/{user.uid}/projects/{project_id}/source/"
        if not asset or asset.bucket != "private-media" or not asset.object_key.startswith(prefix):
            raise RecordNotFound()
        url = assets.signed_read(asset)
        if records.get_project(user.uid, project_id) != project:
            raise RecordConflict()
        return {"expiresAt": utc_now() + timedelta(seconds=300), "assets": {
            "source": {"url": url, "sha256": asset.sha256, "byteSize": asset.byte_size, "contentType": asset.content_type}}}

    @router.post("/api/reconstructions/{result_id}/assets")
    def reconstruction_assets(result_id: Id, user=Depends(active_identity)):
        result = records.get_reconstruction(user.uid, result_id)
        prefix = f"users/{user.uid}/projects/{result.project_id}/reconstruction/{result.job_id}/"
        mapping, attempt = {}, None
        # Check the entire set before minting any capability. Client paths/URLs
        # are never accepted. Manifest relative names are resolved only in this map.
        for asset in result.artifacts:
            if asset.bucket != "private-media" or not asset.object_key.startswith(prefix):
                raise RecordConflict("Artifact outside reconstruction prefix")
            number, separator, name = asset.object_key[len(prefix):].partition("/")
            if not separator or not number.isdecimal() or int(number) < 1 or not name or name in mapping:
                raise RecordConflict("Ambiguous artifact paths")
            if attempt is not None and attempt != number:
                raise RecordConflict("Artifacts cross output attempts")
            attempt = number
            mapping[name] = asset
        signed = {name: {"url": assets.signed_read(asset), "sha256": asset.sha256,
                         "byteSize": asset.byte_size, "contentType": asset.content_type} for name, asset in mapping.items()}
        if records.get_reconstruction(user.uid, result_id) != result:
            raise RecordConflict()
        return {"expiresAt": utc_now() + timedelta(seconds=300), "assets": signed}

    @router.post("/api/projects/{project_id}/media-assets")
    def prepared_assets(project_id: Id, user=Depends(active_identity)):
        project = records.get_project(user.uid, project_id)
        media = project.prepared_media
        if project.state != "ready" or media is None:
            raise RecordNotFound()
        prefix = f"users/{user.uid}/projects/{project_id}/media/"
        mapping = {"video": media.video, **{f"preview-{i:02d}": p.asset for i, p in enumerate(media.previews)}}
        parent = None
        for name, asset in mapping.items():
            if asset.bucket != "private-media" or not asset.object_key.startswith(prefix):
                raise RecordConflict()
            suffix = asset.object_key[len(prefix):].split("/")
            if (len(suffix) != 3 or not re.fullmatch(r"[a-f0-9]{64}", suffix[0])
                    or not suffix[1].isdecimal() or not 1 <= int(suffix[1]) <= 5
                    or suffix[2] != ("video.mp4" if name == "video" else name + ".jpg")):
                raise RecordConflict()
            if parent is not None and parent != suffix[:2]:
                raise RecordConflict()
            parent = suffix[:2]
        signed = {name: {"url": assets.signed_read(asset), "sha256": asset.sha256,
                         "byteSize": asset.byte_size, "contentType": asset.content_type} for name, asset in mapping.items()}
        if records.get_project(user.uid, project_id) != project:
            raise RecordConflict()
        return {"expiresAt": utc_now() + timedelta(seconds=300), "assets": signed}

    return router


def attach_assets(app, records, uploads, assets, active_identity):
    @app.exception_handler(StorageUnavailable)
    async def unavailable(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Private storage is temporarily unavailable; retry later"}, status_code=503)

    @app.exception_handler(StorageQuotaExceeded)
    async def quota(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Free storage capacity reached; remove unused recordings or try later"},
                            status_code=429, headers={"Retry-After": "3600"})

    app.include_router(asset_router(records, uploads, assets, active_identity))
