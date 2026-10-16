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


def test_legacy_job_documents_remain_readable_during_body_profile_upgrade():
    from footylevels.records import JobRecord, KeyframeInput
    job = JobRecord(id='j', owner_id='u', project_id='p', kind='prepareMedia', input_revision=2,
        input_hash='a'*64, profile_version='media-720p30-v1', source_asset=asset())
    assert 'keyframes' not in job.document()  # old strict workers do not know it
    assert 'motionSegment' not in job.document()
    assert JobRecord.model_validate(job.document()) == job
    body = job.model_copy(update={'kind':'reconstructKeyframes', 'profile_version':'body-keyframes-v1',
        'keyframes':(KeyframeInput(frame_index=15, bbox=(0,0,1,1)),)})
    assert body.document()['keyframes'][0]['frameIndex'] == 15


def test_metadata_transaction_retries_read_abort_with_fresh_transaction(monkeypatch):
    from unittest.mock import Mock
    from google.api_core import exceptions
    from footylevels.repositories import FirestoreRecords
    client = Mock()
    transactions = [object(), object()]
    client.transaction.side_effect = transactions
    monkeypatch.setattr('footylevels.repositories.time.sleep', lambda _: None)
    operation = Mock(side_effect=[exceptions.Aborted('fixture read conflict'), 'winner'])
    assert FirestoreRecords(client)._transact(operation) == 'winner'
    assert [call.args[0] for call in operation.call_args_list] == transactions


@pytest.mark.parametrize('error', ['aborted', 'wrapped', 'offline', 'invalid'])
def test_metadata_transaction_fails_bounded_and_preserves_input_errors(monkeypatch, error):
    from unittest.mock import Mock
    from google.api_core import exceptions
    from footylevels.repositories import FirestoreRecords, RecordUnavailable
    failure = exceptions.Aborted('fixture conflict')
    if error == 'wrapped':
        failure = ValueError('SDK retries exhausted')
        failure.__cause__ = exceptions.Aborted('fixture conflict')
    elif error == 'offline': failure = exceptions.ServiceUnavailable('fixture offline')
    elif error == 'invalid': failure = ValueError('invalid selection')
    operation = Mock(side_effect=failure)
    monkeypatch.setattr('footylevels.repositories.time.sleep', lambda _: None)
    with pytest.raises(ValueError if error == 'invalid' else RecordUnavailable):
        FirestoreRecords(Mock())._transact(operation)
    assert operation.call_count == (4 if error in {'aborted', 'wrapped'} else 1)
