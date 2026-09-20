# Module 5.2 verification evidence

Status: **not ready for sign-off**.

## Blocking results

- Deployed integration suite: **18 passed, 5 failed**. All five pipeline
  failures were caused by a real Gemini daily-quota `429 RESOURCE_EXHAUSTED`;
  the resulting runs persisted as `FAILED_INFRASTRUCTURE`.
- Live rules evaluation: loaded 38 cases, then stopped on case 1 because the
  same real Gemini daily quota was exhausted. It therefore did not produce
  38 per-case results.
- Prod stack: **not deployed**. Only the dev Gemini secret exists, so there is
  no truthful second stack-deploy output or set of prod resource names.

## Passing results

- Unit suite: **266 passed**.
- Deployed non-pipeline integration paths: **18 passed**, including auth,
  tenancy, claim CRUD, corrupt-document handling/DLQ, and sweep deduplication.
- Statutory precedence verification: **passed** after restoring deterministic
  Scheme-over-Charter enforcement.
- Frontend lint: exit 0 with four warnings.
- Frontend production build: passed.
- Final dev SAM deployment: passed.

## Dashboard

- `dashboard-live.png`: live CloudWatch-rendered widgets.
- `dashboard-raw.json`: raw `GetDashboard` response with parsed DashboardBody.
- The terminal-status widget contains distinct dimensioned series for
  `COMPLETED_WITH_ABSTENTION`, `FAILED_INFRASTRUCTURE`, and `FAILED_VALIDATION`.

## Live dependency failures

- `failure-gemini-429.json`: real Google 429 logs, persisted DynamoDB item,
  and exact GetRun response body.
- `failure-dynamodb-throttle.json`: real
  `ProvisionedThroughputExceededException`, Lambda logs, and exact HTTP 500.
- `failure-s3-unavailable.json`: scoped live S3 explicit-deny PUT, raw 403 XML,
  and the exact frontend-visible `UPLOAD_FAILED` body.
- `restoration-check.json`: confirms injected settings were restored.

## Retry audit and raw command output

- `retry-audit.txt`
- `unit-tests-full.txt`
- `integration-tests-full.txt`
- `quote-override-eval.txt`
- `rules-eval-full.txt`
- `frontend-lint.txt`
- `frontend-build.txt`
- `dev-deploy-output.txt`
- `prod-deploy-blocker.json`
