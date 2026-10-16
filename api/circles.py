"""Authenticated, deliberately private community routes."""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from footylevels.circles import (AddComment, CreateCircle, JoinCircle, Key,
                                ShareAttempt, public_comment, public_post)
from footylevels.records import Id, utc_now
from footylevels.repositories import RecordConflict


def circle_router(circles, assets, active_identity):
    router = APIRouter()

    @router.get("/api/circles")
    def list_circles(user=Depends(active_identity)):
        return {"items": circles.list_circles(user.uid)}

    @router.post("/api/circles", status_code=201)
    def create_circle(body: CreateCircle, user=Depends(active_identity)):
        return circles.create_circle(user.uid, body.name, body.description)

    @router.post("/api/circles/join")
    def join_circle(body: JoinCircle, user=Depends(active_identity)):
        return circles.join(user.uid, body.code)

    @router.get("/api/circles/{circle_id}")
    def get_circle(circle_id: Id, user=Depends(active_identity)):
        return circles.get_circle(user.uid, circle_id)

    @router.post("/api/circles/{circle_id}/invite")
    def invite(circle_id: Id, user=Depends(active_identity)):
        return circles.invite(user.uid, circle_id)

    @router.delete("/api/circles/{circle_id}")
    def delete(circle_id: Id, user=Depends(active_identity)):
        circles.delete_circle(user.uid, circle_id)
        return {"state": "deleted"}

    @router.delete("/api/circles/{circle_id}/members/{member_id}")
    def remove_member(circle_id: Id, member_id: Id, user=Depends(active_identity)):
        circles.remove_member(user.uid, circle_id, member_id)
        return {"state": "removed"}

    @router.get("/api/circles/{circle_id}/posts")
    def list_posts(circle_id: Id, limit: int = Query(20, ge=1, le=50),
                   cursor: str | None = Query(None, max_length=2048), user=Depends(active_identity)):
        items, next_cursor = circles.list_posts(user.uid, circle_id, limit, cursor)
        return {"items": [public_post(item) for item in items], "nextCursor": next_cursor}

    @router.post("/api/circles/{circle_id}/posts", status_code=201)
    def share(circle_id: Id, body: ShareAttempt, idempotency_key: Annotated[Key, Header()],
              user=Depends(active_identity)):
        return public_post(circles.create_post(user.uid, circle_id, body.project_id, body.skill,
            body.note, idempotency_key, body.previous_post_id))

    @router.get("/api/circles/{circle_id}/posts/{post_id}")
    def post(circle_id: Id, post_id: Id, user=Depends(active_identity)):
        return public_post(circles.get_post(user.uid, circle_id, post_id))

    @router.delete("/api/circles/{circle_id}/posts/{post_id}")
    def remove_post(circle_id: Id, post_id: Id, user=Depends(active_identity)):
        circles.remove_post(user.uid, circle_id, post_id)
        return {"state": "removed"}

    @router.post("/api/circles/{circle_id}/posts/{post_id}/assets")
    def grant(circle_id: Id, post_id: Id, user=Depends(active_identity)):
        if assets is None:
            raise HTTPException(503, "Private storage is temporarily unavailable; retry later")
        post = circles.get_post(user.uid, circle_id, post_id)
        # Expiry is measured before I/O so the advertised deadline never exceeds
        # the actual capability lifetime. The capability is never persisted.
        expires_at = utc_now() + timedelta(seconds=60)
        asset = post.video.asset
        url = assets.signed_read(asset, seconds=60)
        if public_post(circles.get_post(user.uid, circle_id, post_id)) != public_post(post):
            raise RecordConflict()
        return {"expiresAt": expires_at, "assets": {"video": {"url": url,
            "sha256": asset.sha256, "byteSize": asset.byte_size, "contentType": asset.content_type}}}

    @router.get("/api/circles/{circle_id}/posts/{post_id}/comments")
    def comments(circle_id: Id, post_id: Id, limit: int = Query(30, ge=1, le=50),
                 cursor: str | None = Query(None, max_length=2048), user=Depends(active_identity)):
        items, next_cursor = circles.list_comments(user.uid, circle_id, post_id, limit, cursor)
        return {"items": [public_comment(item) for item in items], "nextCursor": next_cursor}

    @router.post("/api/circles/{circle_id}/posts/{post_id}/comments", status_code=201)
    def comment(circle_id: Id, post_id: Id, body: AddComment,
                idempotency_key: Annotated[Key, Header()], user=Depends(active_identity)):
        return public_comment(circles.create_comment(user.uid, circle_id, post_id, body.text,
            body.timestamp, idempotency_key))

    @router.delete("/api/circles/{circle_id}/posts/{post_id}/comments/{comment_id}")
    def remove_comment(circle_id: Id, post_id: Id, comment_id: Id, user=Depends(active_identity)):
        circles.remove_comment(user.uid, circle_id, post_id, comment_id)
        return {"state": "removed"}

    return router
