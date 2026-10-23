"""Private training circles: transactional membership, explicit sharing and revocation.

Only verified canonical playback and explicitly selected completed body motion are
shared. No storage copy or bearer capability is persisted here. Every read consults
current membership, account, project and selected-result provenance fences.
"""
import base64
import binascii
from datetime import datetime, timedelta
import hashlib
import json
import re
import secrets
from typing import Annotated, Literal

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import AwareDatetime, BeforeValidator, Field, model_validator

from .model_requests import MOTION_PROFILE, motion_input_hash
from .records import (AssetRef, Id, JobRecord, KeyframeInput, Metadata, MotionSegment,
                      ProjectRecord, ReconstructionRecord, Sha256, UserRecord, utc_now)
from .repositories import FirestoreRecords, RecordConflict, RecordNotFound


def trimmed(value):
    return value.strip() if isinstance(value, str) else value


Name = Annotated[str, BeforeValidator(trimmed), Field(min_length=1, max_length=80, strict=True)]
Description = Annotated[str, BeforeValidator(trimmed), Field(max_length=240, strict=True)]
Skill = Annotated[str, BeforeValidator(trimmed), Field(min_length=1, max_length=60, strict=True)]
Note = Annotated[str, BeforeValidator(trimmed), Field(max_length=500, strict=True)]
CommentText = Annotated[str, BeforeValidator(trimmed), Field(min_length=1, max_length=500, strict=True)]
Key = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")]
InviteCode = Annotated[str, BeforeValidator(trimmed), Field(pattern=r"^[a-zA-Z0-9_-]{43}$")]


class CreateCircle(Metadata):
    name: Name
    description: Description = ""


class JoinCircle(Metadata):
    code: InviteCode


class ShareAttempt(Metadata):
    project_id: Id
    skill: Skill
    note: Note = ""
    previous_post_id: Id | None = None
    reconstruction_id: Id | None = None

    def document(self):
        payload = super().document()
        # Video-only requests must retain their original idempotency digest,
        # whether this new optional field is omitted or explicitly null.
        if self.reconstruction_id is None:
            payload.pop("reconstructionId", None)
        return payload


class AddComment(Metadata):
    text: CommentText
    timestamp: float | None = Field(default=None, ge=0, strict=True, allow_inf_nan=False)


class Member(Metadata):
    uid: Id
    display_name: str = Field(max_length=80)
    joined_at: AwareDatetime
    generation: str = Field(min_length=32, max_length=64)


class CircleRecord(Metadata):
    id: Id
    name: str = Field(max_length=80)
    description: str = Field(max_length=240)
    owner_id: Id
    created_at: AwareDatetime
    state: Literal["active", "deleted"] = "active"
    member_ids: tuple[Id, ...] = Field(max_length=20)
    members: tuple[Member, ...] = Field(max_length=20)
    invite_hash: str | None = None
    post_count: int = Field(default=0, ge=0, le=200)

    @model_validator(mode="after")
    def distinct_members(self):
        if (len(set(self.member_ids)) != len(self.member_ids)
                or tuple(m.uid for m in self.members) != self.member_ids
                or self.state == "active" and self.owner_id not in self.member_ids):
            raise ValueError("Invalid circle membership")
        return self


class SharedVideo(Metadata):
    duration: float = Field(gt=0, le=60.05, allow_inf_nan=False)
    width: int = Field(gt=0, le=1280)
    height: int = Field(gt=0, le=720)
    fps: Literal[30]
    frame_count: int = Field(ge=30, le=1802)
    asset: AssetRef


class SharedMotionJob(Metadata):
    """Only the immutable playback provenance understood by the mobile client.

    A full worker job contains publication leases, worker IDs and scheduling
    internals. Those never belong in a circle post, even for the author.
    """
    id: Id
    owner_id: Id
    project_id: Id
    input_revision: int
    kind: Literal["reconstructMotion"]
    status: Literal["succeeded", "partial"]
    stage: Literal["ready", "partial"]
    attempt_count: int = Field(ge=1, le=5)
    input_hash: Sha256
    profile_version: Literal["body-motion-30fps-v1"]
    source_asset: AssetRef
    motion_segment: MotionSegment
    keyframes: tuple[KeyframeInput, ...] | None = None
    reconstruction_id: Id
    error_code: str | None = None


class SharedMotion(Metadata):
    reconstruction: ReconstructionRecord
    job: SharedMotionJob


class PostRecord(Metadata):
    id: Id
    circle_id: Id
    author_id: Id
    author_name: str = Field(max_length=80)
    project_id: Id
    project_title: str = Field(max_length=120)
    skill: str = Field(max_length=60)
    note: str = Field(max_length=500)
    created_at: AwareDatetime
    previous_post_id: Id | None = None
    video: SharedVideo | None
    motion: SharedMotion | None = None
    motion_job_fingerprint: Sha256 | None = None
    input_revision: int = Field(ge=1)
    author_membership: str
    state: Literal["active", "removed"] = "active"
    creation_hash: str
    comment_count: int = Field(default=0, ge=0, le=200)


class CommentRecord(Metadata):
    id: Id
    circle_id: Id
    post_id: Id
    author_id: Id
    author_name: str = Field(max_length=80)
    text: str = Field(max_length=500)
    timestamp: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    created_at: AwareDatetime
    author_membership: str
    state: Literal["active", "removed"] = "active"
    creation_hash: str


def public_post(post):
    return post.model_dump(mode="json", by_alias=True,
        exclude={"input_revision", "author_membership", "state", "creation_hash", "comment_count", "motion_job_fingerprint"})


def public_comment(comment):
    return comment.model_dump(mode="json", by_alias=True,
        exclude={"circle_id", "post_id", "author_membership", "state", "creation_hash"})


class FirestoreCircles(FirestoreRecords):
    MAX_CIRCLES = 20
    MAX_MEMBERS = 20
    MAX_POSTS = 200
    MAX_COMMENTS = 200
    SCAN = 100

    def _run(self, operation):
        return self._transact(firestore.transactional(operation))

    def _active(self, transaction, uid):
        profile = self._record(self._ref("users", uid).get(transaction=transaction), UserRecord)
        if profile.account_state != "active":
            raise RecordNotFound()
        return profile

    def _context(self, transaction, uid, circle_id):
        caller = self._active(transaction, uid)
        circle = self._record(self._ref("circles", circle_id).get(transaction=transaction), CircleRecord)
        if circle.state != "active" or uid not in circle.member_ids:
            raise RecordNotFound()
        if circle.owner_id != uid:
            self._active(transaction, circle.owner_id)
        member = next(m for m in circle.members if m.uid == uid)
        return circle, caller, member

    def _public_circle(self, transaction, circle):
        # Deleted/restricted names are not returned while durable cleanup catches up.
        members = []
        for member in circle.members:
            try:
                profile = self._active(transaction, member.uid)
            except RecordNotFound:
                continue
            members.append({"uid": member.uid, "displayName": profile.display_name,
                            "joinedAt": member.joined_at})
        return {"id": circle.id, "name": circle.name, "description": circle.description,
                "ownerId": circle.owner_id, "createdAt": circle.created_at,
                "memberCount": len(members), "members": members}

    def _ledger(self, transaction, uid):
        ref = self._ref("circleMemberships", uid)
        raw = ref.get(transaction=transaction).to_dict() or {}
        return ref, list(raw.get("circleIds", []))

    def create_circle(self, uid, name, description=""):
        body = CreateCircle(name=name, description=description)
        ref = self.client.collection("circles").document()
        now = utc_now()
        def create(transaction):
            profile = self._active(transaction, uid)
            ledger, identifiers = self._ledger(transaction, uid)
            if len(identifiers) >= self.MAX_CIRCLES:
                raise RecordConflict("Circle limit reached")
            member = Member(uid=uid, display_name=profile.display_name, joined_at=now,
                            generation=secrets.token_hex(16))
            circle = CircleRecord(id=ref.id, name=body.name, description=body.description,
                owner_id=uid, created_at=now, member_ids=(uid,), members=(member,))
            result = self._public_circle(transaction, circle)
            transaction.create(ref, circle.document())
            transaction.set(ledger, {"circleIds": [*identifiers, ref.id]})
            return result
        return self._run(create)

    def get_circle(self, uid, circle_id):
        def read(transaction):
            circle, _, _ = self._context(transaction, uid, circle_id)
            return self._public_circle(transaction, circle)
        return self._run(read)

    def list_circles(self, uid):
        # A transactionally maintained per-user ledger bounds this list at 20.
        def read(transaction):
            self._active(transaction, uid)
            _, identifiers = self._ledger(transaction, uid)
            items = []
            for identifier in identifiers[:self.MAX_CIRCLES]:
                try:
                    circle, _, _ = self._context(transaction, uid, identifier)
                    items.append(self._public_circle(transaction, circle))
                except RecordNotFound:
                    continue
            return sorted(items, key=lambda item: (item["createdAt"], item["id"]), reverse=True)
        return self._run(read)

    def invite(self, uid, circle_id):
        code = secrets.token_urlsafe(32)
        digest = hashlib.sha256(code.encode()).hexdigest()
        ref = self._ref("circleInvites", digest)
        expires = utc_now() + timedelta(days=7)
        def rotate(transaction):
            circle, _, _ = self._context(transaction, uid, circle_id)
            if circle.owner_id != uid:
                raise RecordNotFound()
            previous = self._ref("circleInvites", circle.invite_hash) if circle.invite_hash else None
            transaction.create(ref, {"circleId": circle_id, "ownerId": uid, "expiresAt": expires})
            transaction.update(self._ref("circles", circle_id), {"inviteHash": digest})
            if previous:
                transaction.delete(previous)
            return {"code": code, "expiresAt": expires}
        return self._run(rotate)

    def join(self, uid, code):
        body = JoinCircle(code=code)
        digest = hashlib.sha256(body.code.encode()).hexdigest()
        def join(transaction):
            profile = self._active(transaction, uid)
            invite = self._ref("circleInvites", digest).get(transaction=transaction)
            if not invite.exists:
                raise RecordNotFound()
            raw = invite.to_dict()
            if raw["expiresAt"] <= utc_now():
                raise RecordNotFound()
            circle = self._record(self._ref("circles", raw["circleId"]).get(transaction=transaction), CircleRecord)
            self._active(transaction, circle.owner_id)
            ledger, identifiers = self._ledger(transaction, uid)
            if circle.state != "active" or circle.invite_hash != digest:
                raise RecordNotFound()
            if uid in circle.member_ids:
                return self._public_circle(transaction, circle)
            if len(circle.members) >= self.MAX_MEMBERS or len(identifiers) >= self.MAX_CIRCLES:
                raise RecordConflict("Membership limit reached")
            member = Member(uid=uid, display_name=profile.display_name, joined_at=utc_now(), generation=secrets.token_hex(16))
            updated = CircleRecord.model_validate({**circle.document(),
                "memberIds": [*circle.member_ids, uid], "members": [*[m.document() for m in circle.members], member.document()]})
            result = self._public_circle(transaction, updated)
            transaction.set(self._ref("circles", circle.id), updated.document())
            transaction.set(ledger, {"circleIds": [*identifiers, circle.id]})
            return result
        return self._run(join)

    def remove_member(self, uid, circle_id, member_id):
        def remove(transaction):
            circle, _, _ = self._context(transaction, uid, circle_id)
            if uid != member_id and circle.owner_id != uid:
                raise RecordNotFound()
            if member_id == circle.owner_id:
                raise RecordConflict("Delete the circle instead of removing its owner")
            if member_id not in circle.member_ids:
                raise RecordNotFound()
            ledger, identifiers = self._ledger(transaction, member_id)
            members = tuple(m for m in circle.members if m.uid != member_id)
            updated = CircleRecord.model_validate({**circle.document(), "members": [m.document() for m in members],
                "memberIds": [m.uid for m in members],
                "inviteHash": None if uid == circle.owner_id else circle.invite_hash})
            transaction.set(self._ref("circles", circle_id), updated.document())
            transaction.set(ledger, {"circleIds": [i for i in identifiers if i != circle_id]})
            if uid == circle.owner_id and circle.invite_hash:
                # A removed member must not rejoin with the invitation they know.
                transaction.delete(self._ref("circleInvites", circle.invite_hash))
        self._run(remove)

    def _delete_circle(self, transaction, circle):
        # All reads before writes; release bounded user ledgers atomically.
        ledgers = [(m.uid, *self._ledger(transaction, m.uid)) for m in circle.members]
        ref = self._ref("circles", circle.id)
        transaction.update(ref, {"state": "deleted", "name": "", "description": "", "members": [], "memberIds": [], "inviteHash": None})
        if circle.invite_hash:
            transaction.delete(self._ref("circleInvites", circle.invite_hash))
        for _, ledger, identifiers in ledgers:
            transaction.set(ledger, {"circleIds": [i for i in identifiers if i != circle.id]})
        transaction.set(self._ref("circleCleanup", circle.id), {"circleId": circle.id, "ownerId": circle.owner_id})

    def delete_circle(self, uid, circle_id):
        def remove(transaction):
            circle, _, _ = self._context(transaction, uid, circle_id)
            if circle.owner_id != uid:
                raise RecordNotFound()
            self._delete_circle(transaction, circle)
        self._run(remove)

    @staticmethod
    def _media_path(project, asset):
        prefix = f"users/{project.owner_id}/projects/{project.id}/media/"
        suffix = asset.object_key[len(prefix):].split("/") if asset.object_key.startswith(prefix) else []
        if (asset.bucket != "private-media" or asset.content_type != "video/mp4" or len(suffix) != 3
                or not re.fullmatch(r"[a-f0-9]{64}", suffix[0])
                or not re.fullmatch(r"[1-5]", suffix[1]) or suffix[2] != "video.mp4"):
            raise RecordConflict("Playback provenance is unavailable")

    @staticmethod
    def _job_fingerprint(job):
        return hashlib.sha256(job.model_dump_json(by_alias=True).encode()).hexdigest()

    @staticmethod
    def motion_artifacts(motion):
        """Validate the complete selected output before signing any one file."""
        result, job = motion.reconstruction, motion.job
        prefix = f"users/{result.owner_id}/projects/{result.project_id}/reconstruction/{job.id}/{job.attempt_count}/"
        mapping = {}
        types = {"sequence.json": "application/json", "faces.bin": "application/octet-stream",
                 "video.mp4": "video/mp4"}
        chunk_names = {f"body-{start:06d}.bin.gz" for start in range(0, result.requested_frame_count, 30)}
        for asset in result.artifacts:
            if asset.bucket != "private-media" or not asset.object_key.startswith(prefix):
                raise RecordConflict("Motion artifact provenance is unavailable")
            name = asset.object_key[len(prefix):]
            content_type = types.get(name)
            if name in chunk_names:
                content_type = "application/gzip"
            elif re.fullmatch(r"preview-[0-9]{4}\.jpg", name) and int(name[8:12]) < result.requested_frame_count:
                content_type = "image/jpeg"
            if not content_type or asset.content_type != content_type or name in mapping:
                raise RecordConflict("Unsupported motion artifact")
            mapping[name] = asset
        if not (set(types) | chunk_names) <= mapping.keys():
            raise RecordConflict("Motion artifact set is incomplete")
        return mapping

    def _selected_motion(self, transaction, project, reconstruction_id):
        result = self._record(self._ref("reconstructions", reconstruction_id).get(transaction=transaction), ReconstructionRecord)
        job = self._record(self._ref("jobs", result.job_id).get(transaction=transaction), JobRecord)
        if result.owner_id != project.owner_id or result.project_id != project.id:
            raise RecordNotFound()
        segment = job.motion_segment
        source = project.prepared_media.video
        if (result.id != result.job_id or job.id != result.id
                or (job.owner_id, job.project_id, job.input_revision, job.source_asset, job.profile_version)
                    != (project.owner_id, project.id, project.input_revision, source, MOTION_PROFILE)
                or (result.owner_id, result.project_id, result.input_revision, result.source_asset, result.profile_version)
                    != (project.owner_id, project.id, project.input_revision, source, MOTION_PROFILE)
                or result.mode != "motion" or result.contract.encoding != "motionBuffers"
                or result.contract.coordinate_system != "rightHandedYUp" or result.contract.units != "meters"
                or job.kind != "reconstructMotion" or job.keyframes is not None or segment is None
                or segment.end_frame > project.prepared_media.frame_count
                or job.input_hash != motion_input_hash(source, segment)
                or job.reconstruction_id != result.id or job.finished_at is None or job.error_code is not None
                or not 1 <= job.attempt_count <= 5
                or job.status != ("succeeded" if result.outcome == "ready" else "partial")
                or job.stage != ("ready" if result.outcome == "ready" else "partial")
                or result.requested_frame_count != segment.end_frame - segment.start_frame):
            raise RecordConflict("Only verified completed body motion can be shared")
        fields = SharedMotionJob.model_fields
        shared_job = SharedMotionJob.model_validate({name: getattr(job, name) for name in fields})
        motion = SharedMotion(reconstruction=result, job=shared_job)
        self.motion_artifacts(motion)
        return motion, self._job_fingerprint(job)

    def _visible_post(self, transaction, uid, circle_id, post_id, context=None):
        circle, _, _ = context or self._context(transaction, uid, circle_id)
        post = self._record(self._ref("circlePosts", post_id).get(transaction=transaction), PostRecord)
        if post.circle_id != circle_id or post.state != "active" or post.video is None:
            raise RecordNotFound()
        author_member = next((m for m in circle.members if m.uid == post.author_id), None)
        if author_member is None or author_member.generation != post.author_membership:
            raise RecordNotFound()
        self._active(transaction, post.author_id)
        project = self._record(self._ref("projects", post.project_id).get(transaction=transaction), ProjectRecord)
        if (project.owner_id != post.author_id or project.state != "ready" or project.deleted_at is not None
                or project.prepared_media is None or project.input_revision != post.input_revision
                or project.prepared_media.video != post.video.asset):
            raise RecordNotFound()
        media = project.prepared_media
        if (post.video.duration, post.video.width, post.video.height, post.video.fps, post.video.frame_count) != (
                media.duration, media.width, media.height, media.fps, media.frame_count):
            raise RecordNotFound()
        self._media_path(project, post.video.asset)
        if post.motion is not None:
            try:
                motion, fingerprint = self._selected_motion(transaction, project, post.motion.reconstruction.id)
                if motion != post.motion or fingerprint != post.motion_job_fingerprint:
                    raise RecordNotFound()
            except (RecordConflict, ValueError):
                raise RecordNotFound() from None
        return post

    def get_post(self, uid, circle_id, post_id):
        return self._run(lambda transaction: self._visible_post(transaction, uid, circle_id, post_id))

    @staticmethod
    def _creation(body, uid, circle_id, key, kind, post_id=None):
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", key):
            raise ValueError("Expected Idempotency-Key")
        digest = hashlib.sha256(json.dumps(body.document(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        identifier = hashlib.sha256(json.dumps([uid, circle_id, post_id, key, kind], separators=(",", ":")).encode()).hexdigest()
        return identifier, digest

    def create_post(self, uid, circle_id, project_id, skill, note, key, previous_post_id=None, reconstruction_id=None):
        body = ShareAttempt(project_id=project_id, skill=skill, note=note, previous_post_id=previous_post_id,
                            reconstruction_id=reconstruction_id)
        identifier, digest = self._creation(body, uid, circle_id, key, "post")
        ref = self._ref("circlePosts", identifier)
        def create(transaction):
            context = self._context(transaction, uid, circle_id)
            circle, profile, membership = context
            existing = ref.get(transaction=transaction)
            if existing.exists:
                post = self._visible_post(transaction, uid, circle_id, identifier, context)
                if post.creation_hash != digest:
                    raise RecordConflict("Idempotency-Key was reused")
                return post
            if circle.post_count >= self.MAX_POSTS:
                raise RecordConflict("Circle attempt limit reached")
            project = self._record(self._ref("projects", body.project_id).get(transaction=transaction), ProjectRecord)
            if project.owner_id != uid or project.state in {"deleting", "deleted"} or project.deleted_at:
                raise RecordNotFound()
            if project.state != "ready" or project.prepared_media is None:
                raise RecordConflict("Wait for prepared playback")
            media = project.prepared_media
            self._media_path(project, media.video)
            motion, fingerprint = (None, None)
            if body.reconstruction_id is not None:
                motion, fingerprint = self._selected_motion(transaction, project, body.reconstruction_id)
            if body.previous_post_id:
                previous = self._visible_post(transaction, uid, circle_id, body.previous_post_id, context)
                if previous.author_id != uid:
                    raise RecordNotFound()
            post = PostRecord(id=identifier, circle_id=circle_id, author_id=uid, author_name=profile.display_name,
                project_id=project.id, project_title=project.title, skill=body.skill, note=body.note,
                previous_post_id=body.previous_post_id, created_at=utc_now(), input_revision=project.input_revision,
                author_membership=membership.generation, creation_hash=digest,
                motion=motion, motion_job_fingerprint=fingerprint,
                video=SharedVideo(duration=media.duration, width=media.width, height=media.height,
                    fps=media.fps, frame_count=media.frame_count, asset=media.video))
            transaction.create(ref, post.document())
            transaction.update(self._ref("circles", circle_id), {"postCount": circle.post_count + 1})
            return post
        return self._run(create)

    def remove_post(self, uid, circle_id, post_id):
        def remove(transaction):
            circle, _, _ = self._context(transaction, uid, circle_id)
            post = self._record(self._ref("circlePosts", post_id).get(transaction=transaction), PostRecord)
            if post.circle_id != circle_id or uid not in {post.author_id, circle.owner_id}:
                raise RecordNotFound()
            transaction.update(self._ref("circlePosts", post_id), {"state": "removed", "note": "", "skill": "",
                "projectTitle": "", "authorName": "", "video": None, "motion": None, "motionJobFingerprint": None})
        self._run(remove)

    def _comment(self, transaction, circle, snapshot, duration):
        comment = self._record(snapshot, CommentRecord)
        member = next((m for m in circle.members if m.uid == comment.author_id), None)
        if (comment.circle_id != circle.id or comment.state != "active" or member is None
                or member.generation != comment.author_membership
                or comment.timestamp is not None and comment.timestamp >= duration):
            raise RecordNotFound()
        self._active(transaction, comment.author_id)
        return comment

    def create_comment(self, uid, circle_id, post_id, text, timestamp, key):
        body = AddComment(text=text, timestamp=timestamp)
        identifier, digest = self._creation(body, uid, circle_id, key, "comment", post_id)
        ref = self._ref("circleComments", identifier)
        def create(transaction):
            context = self._context(transaction, uid, circle_id)
            circle, profile, membership = context
            post = self._visible_post(transaction, uid, circle_id, post_id, context)
            existing = ref.get(transaction=transaction)
            if body.timestamp is not None and body.timestamp >= post.video.duration:
                raise ValueError("Timestamp is outside playback")
            if existing.exists:
                comment = self._comment(transaction, circle, existing, post.video.duration)
                if comment.post_id != post_id or comment.creation_hash != digest:
                    raise RecordConflict("Idempotency-Key was reused")
                return comment
            if post.comment_count >= self.MAX_COMMENTS:
                raise RecordConflict("Attempt feedback limit reached")
            comment = CommentRecord(id=identifier, circle_id=circle_id, post_id=post_id, author_id=uid,
                author_name=profile.display_name, text=body.text, timestamp=body.timestamp, created_at=utc_now(),
                author_membership=membership.generation, creation_hash=digest)
            transaction.create(ref, comment.document())
            transaction.update(self._ref("circlePosts", post_id), {"commentCount": post.comment_count + 1})
            return comment
        return self._run(create)

    def remove_comment(self, uid, circle_id, post_id, comment_id):
        def remove(transaction):
            context = self._context(transaction, uid, circle_id)
            circle, _, _ = context
            self._visible_post(transaction, uid, circle_id, post_id, context)
            comment = self._record(self._ref("circleComments", comment_id).get(transaction=transaction), CommentRecord)
            if (comment.circle_id != circle_id or comment.post_id != post_id
                    or uid not in {comment.author_id, circle.owner_id}):
                raise RecordNotFound()
            transaction.update(self._ref("circleComments", comment_id), {"state": "removed", "text": "", "authorName": "", "timestamp": None})
        self._run(remove)

    @staticmethod
    def _cursor(uid, scope, snapshot):
        return base64.urlsafe_b64encode(json.dumps({"uid": uid, "scope": scope, "id": snapshot.id,
            "createdAt": snapshot.to_dict()["createdAt"].isoformat()}, separators=(",", ":")).encode()).decode()

    def _page(self, uid, scope, collection, field, value, limit, cursor, visible):
        query = self.client.collection(collection).where(filter=FieldFilter(field, "==", value)).order_by(
            "createdAt", direction=firestore.Query.DESCENDING).order_by("__name__")
        if cursor:
            try:
                raw = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                created = datetime.fromisoformat(raw["createdAt"])
                if raw["uid"] != uid or raw["scope"] != scope or created.tzinfo is None or len(cursor) > 2048:
                    raise ValueError()
                query = query.start_after({"createdAt": created, "__name__": self._ref(collection, raw["id"])})
            except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error):
                raise ValueError("Invalid community cursor") from None
        snapshots = list(query.limit(self.SCAN).stream())
        items, last = [], None
        for index, snapshot in enumerate(snapshots):
            last = snapshot
            try:
                item = visible(snapshot.id)
            except RecordNotFound:
                continue
            items.append(item)
            if len(items) == limit:
                break
        has_more = last is not None and (index + 1 < len(snapshots) or len(snapshots) == self.SCAN)
        return items, self._cursor(uid, scope, last) if has_more else None

    def list_posts(self, uid, circle_id, limit=20, cursor=None):
        if not 1 <= limit <= 50:
            raise ValueError("Invalid page size")
        self.get_circle(uid, circle_id)
        result = self._page(uid, circle_id, "circlePosts", "circleId", circle_id, limit, cursor,
                            lambda identifier: self.get_post(uid, circle_id, identifier))
        self.get_circle(uid, circle_id)
        return result

    def list_comments(self, uid, circle_id, post_id, limit=30, cursor=None):
        if not 1 <= limit <= 50:
            raise ValueError("Invalid page size")
        self.get_post(uid, circle_id, post_id)
        def visible(identifier):
            def read(transaction):
                context = self._context(transaction, uid, circle_id)
                post = self._visible_post(transaction, uid, circle_id, post_id, context)
                comment = self._comment(transaction, context[0], self._ref("circleComments", identifier).get(transaction=transaction), post.video.duration)
                if comment.post_id != post_id:
                    raise RecordNotFound()
                return comment
            return self._run(read)
        result = self._page(uid, f"{circle_id}/{post_id}", "circleComments", "postId", post_id, limit, cursor, visible)
        self.get_post(uid, circle_id, post_id)
        return result

    def _purge_posts(self, field, value, budget):
        for snapshot in self.client.collection("circlePosts").where(filter=FieldFilter(field, "==", value)).limit(budget).stream():
            comments = list(self.client.collection("circleComments").where(
                filter=FieldFilter("postId", "==", snapshot.id)).limit(budget).stream())
            for comment in comments:
                comment.reference.delete()
                budget -= 1
            if budget <= 0:
                return False
            snapshot.reference.delete()
            budget -= 1
            if budget <= 0:
                return False
        return not list(self.client.collection("circlePosts").where(filter=FieldFilter(field, "==", value)).limit(1).stream())

    def cleanup_project(self, owner_id, project_id, budget=20):
        # Caller supplies a durable project/account tombstone. No active share can
        # be created once the tombstone exists; both operations read that project.
        project = self._record(self._ref("projects", project_id).get(), ProjectRecord)
        if project.owner_id != owner_id or project.state not in {"deleting", "deleted"}:
            raise RecordConflict("Project cleanup requires a tombstone")
        return self._purge_posts("projectId", project_id, budget)

    def cleanup_account(self, uid, budget=20):
        user = self._record(self._ref("users", uid).get(), UserRecord)
        if user.account_state not in {"deleting", "deleted"}:
            raise RecordConflict("Account cleanup requires a tombstone")
        # Delete owned circles first and remove membership in remaining circles.
        _, identifiers = self._run(lambda transaction: self._ledger(transaction, uid))
        for identifier in identifiers[:1]:
            def remove(transaction):
                snapshot = self._ref("circles", identifier).get(transaction=transaction)
                if not snapshot.exists:
                    ledger, ids = self._ledger(transaction, uid)
                    transaction.set(ledger, {"circleIds": [i for i in ids if i != identifier]})
                    return
                circle = self._record(snapshot, CircleRecord)
                if circle.owner_id == uid and circle.state == "active":
                    self._delete_circle(transaction, circle)
                else:
                    ledger, ids = self._ledger(transaction, uid)
                    members = [m for m in circle.members if m.uid != uid]
                    transaction.update(snapshot.reference, {"members": [m.document() for m in members], "memberIds": [m.uid for m in members]})
                    transaction.set(ledger, {"circleIds": [i for i in ids if i != identifier]})
            self._run(remove)
            return False
        if not self._purge_posts("authorId", uid, budget):
            return False
        comments = list(self.client.collection("circleComments").where(filter=FieldFilter("authorId", "==", uid)).limit(budget).stream())
        for comment in comments:
            comment.reference.delete()
        if comments:
            return False
        self._ref("circleMemberships", uid).delete()
        return True

    def reconcile_cleanup(self, budget=20):
        # Circle deletion is a durable marker. Work is bounded and independent of
        # media storage; deleting a circle never deletes a player's own project.
        for marker in self.client.collection("circleCleanup").limit(1).stream():
            circle_id = marker.to_dict()["circleId"]
            if not self._purge_posts("circleId", circle_id, budget):
                return False
            comments = list(self.client.collection("circleComments").where(filter=FieldFilter("circleId", "==", circle_id)).limit(budget).stream())
            for comment in comments:
                comment.reference.delete()
            if comments:
                return False
            marker.reference.delete()
        return True
