"""Reject unsafe/ambiguous metadata before it reaches a schemaless database."""
import sys
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from footylevels.records import AssetRef, ComparisonRecord, ProjectRecord, UserRecord


def asset(**changes):
    return AssetRef(bucket="private-media", object_key=changes.pop("object_key", "users/u/projects/p/source/a.mp4"),
                    sha256="a" * 64, byte_size=changes.pop("byte_size", 12), content_type="video/mp4", **changes)


@pytest.mark.parametrize("path", ["https://host/file?token=secret", "../secret", "users//file", "/file", "users/../file"])
def test_assets_cannot_hold_urls_or_traversal(path):
    with pytest.raises(ValidationError):
        asset(object_key=path)


def test_assets_bound_free_storage_file_size():
    with pytest.raises(ValidationError):
        asset(byte_size=52428801)


def test_reject_unknown_fields_and_naive_timestamps():
    with pytest.raises(ValidationError):
        UserRecord(id="u", display_name="Player", score=90)
    with pytest.raises(ValidationError):
        UserRecord(id="u", display_name="Player", created_at=datetime(2026, 10, 9))


def test_ready_project_requires_source_provenance():
    with pytest.raises(ValidationError):
        ProjectRecord(id="p", owner_id="u", title="Clip", source="gallery", state="ready")
    record = ProjectRecord(id="p", owner_id="u", title="Clip", source="gallery", state="ready", video=asset())
    assert ProjectRecord.model_validate(record.document()) == record
    assert record.document()["ownerId"] == "u"


def test_reference_comparisons_must_pin_version():
    with pytest.raises(ValidationError):
        ComparisonRecord(id="c", owner_id="u", left_reconstruction_id="r", right_type="reference", right_id="ref", mode="sideBySide")


@pytest.mark.parametrize("uid", ["a/b", "..", "__reserved__"])
def test_ids_cannot_change_document_paths(uid):
    with pytest.raises(ValidationError):
        UserRecord(id=uid, display_name="Player")
