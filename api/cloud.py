"""Authenticated application API factory. Does not expose legacy local data routes."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Annotated
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from firebase_admin import credentials, delete_app, initialize_app
from google.cloud import firestore
from pydantic import Field

from footylevels.authentication import (AuthenticationRejected,
    AuthenticationUnavailable, FirebaseIdentityVerifier, Identity)
from footylevels.records import Id, Metadata
from footylevels.repositories import FirestoreRecords, RecordConflict, RecordNotFound, RecordUnavailable


class CreateProject(Metadata):
    title: str = Field(min_length=1, max_length=120)
    source: Annotated[str, Field(pattern=r"^(capture|gallery)$")]
    label: str | None = Field(default=None, min_length=1, max_length=80)


class RenameProject(Metadata):
    title: str = Field(min_length=1, max_length=120)


def create_app(records, verifier, lifespan=None, *, assets=None, uploads=None) -> FastAPI:
    app = FastAPI(title="FootyLevels", version="0.3.0", lifespan=lifespan)

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Authorization"
        return response

    @app.exception_handler(RecordNotFound)
    async def inaccessible(request, error):
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    @app.exception_handler(RecordConflict)
    async def conflict(request, error):
        return JSONResponse({"detail": "Resource state changed; refresh and retry"}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.exception_handler(RecordUnavailable)
    async def metadata_unavailable(request, error):
        return JSONResponse({"detail": "Service temporarily unavailable"}, status_code=503)

    def identity(request: Request) -> Identity:
        values = request.headers.getlist("authorization")
        parts = values[0].split() if len(values) == 1 else []
        if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 16384:
            raise HTTPException(401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"})
        try:
            return verifier.verify(parts[1])
        except AuthenticationRejected:
            raise HTTPException(401, "Session expired or unavailable; sign in again",
                                headers={"WWW-Authenticate": "Bearer"}) from None
        except AuthenticationUnavailable:
            raise HTTPException(503, "Sign-in verification is temporarily unavailable; retry later") from None

    def active_identity(user: Identity = Depends(identity)) -> Identity:
        try:
            profile = records.get_user(user.uid)
        except RecordNotFound:
            raise HTTPException(403, "Complete your account setup") from None
        if profile.account_state != "active":
            raise HTTPException(403, "Account is unavailable")
        return user

    # Future routers must use this dependency; no UID from a request body or URL.
    app.state.active_identity = active_identity
    app.state.records = records

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "FootyLevels", "apiVersion": "0.3.0"}

    @app.post("/api/me/bootstrap")
    def bootstrap(user: Identity = Depends(identity)):
        try:
            return records.bootstrap_user(user.uid, user.display_name)
        except RecordConflict:
            raise HTTPException(403, "Account is unavailable") from None

    @app.get("/api/me")
    def profile(user: Identity = Depends(active_identity)):
        return records.get_user(user.uid)

    @app.post("/api/projects", status_code=201)
    def create_project(body: CreateProject,
                       idempotency_key: str | None = Header(None, pattern=r"^[a-zA-Z0-9_-]{16,80}$"),
                       user: Identity = Depends(active_identity)):
        return records.create_project(user.uid, body.title, body.source, idempotency_key, body.label)

    @app.get("/api/projects")
    def projects(limit: int = Query(20, ge=1, le=50), cursor: str | None = Query(None, max_length=2048),
                 user: Identity = Depends(active_identity)):
        items, next_cursor = records.list_projects(user.uid, limit, cursor)
        return {"items": items, "nextCursor": next_cursor}

    @app.get("/api/projects/{project_id}")
    def project(project_id: Id, user: Identity = Depends(active_identity)):
        return records.get_project(user.uid, project_id)

    @app.patch("/api/projects/{project_id}")
    def rename(project_id: Id, body: RenameProject, user: Identity = Depends(active_identity)):
        return records.rename_project(user.uid, project_id, body.title)

    @app.delete("/api/projects/{project_id}", status_code=202)
    def delete(project_id: Id, user: Identity = Depends(active_identity)):
        record = records.mark_deleting(user.uid, project_id)
        # This is an accepted tombstone; blob cleanup is implemented in FP-17/54.
        return {"id": record.id, "state": record.state}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: Id, user: Identity = Depends(active_identity)):
        record = records.get_job(user.uid, job_id)
        return record.model_dump(mode="json", by_alias=True, exclude={"lease_token", "worker_id", "lease_expires_at"})

    @app.get("/api/reconstructions/{result_id}")
    def reconstruction(result_id: Id, user: Identity = Depends(active_identity)):
        return records.get_reconstruction(user.uid, result_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: Id, user: Identity = Depends(active_identity)):
        record = records.cancel_job(user.uid, job_id)
        return record.model_dump(mode="json", by_alias=True, exclude={"lease_token", "worker_id", "lease_expires_at"})

    @app.get("/api/comparisons/{comparison_id}")
    def comparison(comparison_id: Id, user: Identity = Depends(active_identity)):
        return records.get_comparison(user.uid, comparison_id)

    if assets is not None:
        from api.assets import attach_assets
        from footylevels.uploads import FirestoreUploads
        attach_assets(app, records, uploads or FirestoreUploads(records.client), assets, active_identity)

    return app


def app_factory() -> FastAPI:
    """uvicorn api.cloud:app_factory --factory; requires explicit scoped credentials."""
    from footylevels.assets import SupabaseAssets
    storage = SupabaseAssets(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    project = os.environ["FIREBASE_PROJECT_ID"]
    environments = {
        "footylevels-dev-20261009": ("dev", "https://bwzxrvkxfubnmuieljoj.supabase.co"),
        "footylevels-demo-20261009": ("demo", "https://skzgjwbkadiazfvuhtts.supabase.co"),
    }
    if project not in environments or os.environ["SUPABASE_URL"] != environments[project][1]:
        raise ValueError("Firebase and storage environments must match")
    filename = Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    credential = credentials.Certificate(str(filename))
    if credential.project_id != project or credential.service_account_email != f"footylevels-api-{environments[project][0]}@{project}.iam.gserviceaccount.com":
        raise ValueError("Firebase and runtime credential projects must match")
    firebase = initialize_app(credential, {"projectId": project, "httpTimeout": 15},
                              name="footylevels-" + uuid.uuid4().hex)
    try:
        verifier = FirebaseIdentityVerifier(firebase)
        client = firestore.Client.from_service_account_json(str(filename), project=project)
    except Exception:
        delete_app(firebase)
        raise

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            client.close()
            delete_app(firebase)

    return create_app(FirestoreRecords(client), verifier, lifespan, assets=storage)
