from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from functions.delete_account import app as delete_account


def test_erase_user_data_removes_only_caller_rows_and_prefix():
    claims = MagicMock()
    documents = MagicMock()
    runs = MagicMock()
    transitions = MagicMock()
    quota = MagicMock()
    s3 = MagicMock()

    claims.query.return_value = {"Items": [{"claimId": "c1", "userId": "user-a"}]}
    documents.query.return_value = {
        "Items": [{"documentId": "d1", "userId": "user-a", "s3Key": "user-a/c1/d1.png"}]
    }
    quota.query.return_value = {"Items": [{"dayIso": "2026-09-20", "userId": "user-a"}]}
    runs.query.return_value = {"Items": [{"runId": "r1", "claimId": "c1"}]}
    transitions.query.return_value = {"Items": [{"transitionKey": "t1", "claimId": "c1"}]}
    s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "user-a/c1/d1.png"}],
    }

    counts = delete_account.erase_user_data(
        "user-a",
        claims_table=claims,
        documents_table=documents,
        runs_table=runs,
        transitions_table=transitions,
        quota_table=quota,
        s3=s3,
        bucket="docs",
    )

    assert counts == {
        "claimsRemoved": 1,
        "documentsRemoved": 1,
        "runsRemoved": 1,
        "objectsRemoved": 1,
    }
    claims.delete_item.assert_called_once_with(Key={"userId": "user-a", "claimId": "c1"})
    documents.delete_item.assert_called_once_with(Key={"userId": "user-a", "documentId": "d1"})
    runs.delete_item.assert_called_once_with(Key={"claimId": "c1", "runId": "r1"})
    s3.delete_objects.assert_called_once()
    assert s3.delete_objects.call_args.kwargs["Delete"]["Objects"] == [{"Key": "user-a/c1/d1.png"}]


def test_handler_unauthorized_without_jwt():
    response = delete_account.handler({"requestContext": {}}, SimpleNamespace(aws_request_id="r1"))
    assert response["statusCode"] == 401
