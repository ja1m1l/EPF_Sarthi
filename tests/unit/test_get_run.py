"""Unit tests for GetRun — one run and the newest-first list."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src/shared"))

from functions.get_run import app as get_run  # noqa: E402


_USER = "user-1"
_CLAIM = "claim-1"


def _event(*, run_id: str | None = "run-1", user_id: str = _USER) -> dict:
    params = {"claimId": _CLAIM}
    if run_id is not None:
        params["runId"] = run_id
    return {
        "pathParameters": params,
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": user_id}}}},
    }


def _run_item(run_id: str, started_at: str, status: str = "COMPLETED") -> dict:
    return {
        "claimId": {"S": _CLAIM},
        "runId": {"S": run_id},
        "correlationId": {"S": f"corr-{run_id}"},
        "status": {"S": status},
        "startedAt": {"S": started_at},
        "rulesAgentOutputJson": {"S": json.dumps({"applicable": True, "timelineDays": 20})},
    }


class TestGetRun(unittest.TestCase):
    def test_missing_claim_id_is_400(self):
        event = _event()
        event["pathParameters"] = {"runId": "run-1"}
        resp = get_run.handler(event, MagicMock(aws_request_id="r"))
        self.assertEqual(resp["statusCode"], 400)

    def test_lists_runs_newest_first_when_run_id_omitted(self):
        older = _run_item("run-old", "2026-01-01T00:00:00+00:00")
        newer = _run_item("run-new", "2026-09-01T00:00:00+00:00")
        mock_ddb = MagicMock()
        mock_ddb.get_item.return_value = {"Item": {"claimId": {"S": _CLAIM}}}
        mock_ddb.query.return_value = {"Items": [older, newer]}

        with patch.object(get_run.boto3, "client", return_value=mock_ddb):
            resp = get_run.handler(_event(run_id=None), MagicMock(aws_request_id="r"))

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual([run["runId"] for run in body["runs"]], ["run-new", "run-old"])
        self.assertEqual(body["runs"][0]["rulesAgentOutputJson"]["timelineDays"], 20)

    def test_empty_list_when_claim_has_no_runs(self):
        mock_ddb = MagicMock()
        mock_ddb.get_item.return_value = {"Item": {"claimId": {"S": _CLAIM}}}
        mock_ddb.query.return_value = {"Items": []}

        with patch.object(get_run.boto3, "client", return_value=mock_ddb):
            resp = get_run.handler(_event(run_id=None), MagicMock(aws_request_id="r"))

        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(json.loads(resp["body"])["runs"], [])

    def test_list_is_404_when_claim_is_not_the_caller_s(self):
        mock_ddb = MagicMock()
        mock_ddb.get_item.return_value = {}

        with patch.object(get_run.boto3, "client", return_value=mock_ddb):
            resp = get_run.handler(_event(run_id=None), MagicMock(aws_request_id="r"))

        self.assertEqual(resp["statusCode"], 404)
        mock_ddb.query.assert_not_called()
