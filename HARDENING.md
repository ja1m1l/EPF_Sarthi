# Module 5.2 — Observability and hardening

No new product features. This file is the documented failure contract, retry audit, secrets posture, and deploy commands.

## Deploy (one command per environment)

Dev (existing stack `epf-sentinel-dev`, tables `*-dev`, bucket `epf-sentinel-docs-dev-<account>`):

```bash
make deploy STAGE=dev
```

Prod (stack `epf-sentinel-prod`, tables `*-prod`, bucket `epf-sentinel-docs-prod-<account>`, secret `epf-sentinel/gemini-api-key-prod`):

```bash
make deploy STAGE=prod AMPLIFY_ORIGIN=https://<prod-amplify-app>.amplifyapp.com
```

Create the prod Gemini secret **before** the first prod deploy. The key value is set in Secrets Manager only — never on the CLI.

## Secrets

| Item | Where it lives |
| --- | --- |
| Gemini API key | Secrets Manager only (`GEMINI_SECRET_NAME` is the **name**, not the key) |
| Cognito | AWS-managed; frontend gets pool/client ids, never a secret key |
| JWT | Browser session only; API derives `userId` from `sub` |

Confirmed: no key in `src/`, `frontend/`, `.env.example`, or SAM parameters. `gemini.py` logs `secretName` and `region` on load, never the secret string. HTTP error bodies use `internal_error()` / `too_many_requests()` and never echo exception text from Gemini.

## Abuse / cost controls

- HTTP API stage throttle: `StageSettings` map — dev 100 rps / burst 50; prod 20 / 10.
- Per-user daily analysis cap: `DAILY_ANALYSIS_CAP` (default **20**), enforced in `start_analysis._consume_daily_quota` against `epf-sentinel-AnalysisQuota-${Stage}`. User-visible `429` `{ "error": { "code": "ANALYSIS_CAP_EXCEEDED" } }`.
- Gemini daily call / token alarms on SNS `epf-sentinel-ops-alerts-${Stage}`.

## Retry audit (every loop)

| Location | Bound | Backoff | What it retries |
| --- | --- | --- | --- |
| `src/shared/gemini.py` `_retry` | `max_retries=4` (5 attempts) | exp 1/2/4s, cap 20s, ±0.5s jitter | 429 (not per-day quota) and 5xx only |
| `src/shared/gemini.py` `generate` fallback | 1 fallback model, then `_retry` again | same as `_retry` | retired-model **404** only — **not** 429 |
| `src/shared/rules_agent.py` JSON parse | 2 retries (3 generate calls) | 1/2/4s + ±0.2s jitter | malformed JSON only; Gemini exceptions raise `RulesAgentInfrastructureError` |
| `src/functions/grievance_agent/app.py` ungrounded draft | **1** retry | 0.4s ± jitter | ungrounded draft; then `DRAFT_UNGROUNDED` → infrastructure/validation path |
| Step Functions agent states (`infra/state_machine.asl.json`) | `MaxAttempts: 2` | `IntervalSeconds: 2`, `BackoffRate: 2` | Lambda 5xx / SDK / `States.TaskFailed` — **never** `ValidationError` |
| SQS `DocumentEventQueue` | `maxReceiveCount: 3` | SQS visibility timeout | extract_document re-raise of transient errors → then DLQ |
| Frontend `ClaimDetail` poll | 120s wall clock, 2s interval | none (poll, not error-retry) | `RUNNING` until terminal |
| Frontend `NewClaim` extraction wait | 90s, 2s interval | none (poll) | document `PROCESSING` |

Per-day Gemini quota 429s (`per day` / `free_tier` / `limit: 0`) are **not** retried. They emit `GeminiErrors` with `StatusCode=429` and propagate as infrastructure failure.

## Failure behavior (user-visible)

| Dependency | How it is induced in tests | User-visible result | Persisted run status |
| --- | --- | --- | --- |
| Gemini unavailable / 429 | `gemini.generate` raises `ResourceExhausted` / 429; Rules Agent maps to `RulesAgentInfrastructureError`; SFN Catch → `RecordFailure` | Claim result: **The analysis could not be completed** (`FAILED_INFRASTRUCTURE`). Not an abstention. | `FAILED_INFRASTRUCTURE` |
| DynamoDB throttled on analyze | `ThrottlingException` on claim/document/quota fetch | HTTP **500** `{ "error": { "code": "INTERNAL_ERROR" } }` — no run started | none |
| S3 unavailable on upload | presigned PUT fails or extract cannot `GetObject` | Upload error on New claim; extract retries then DLQ / `NEEDS_MANUAL_ENTRY` for corrupt/policy cases | analysis not started if upload fails |

A Gemini outage must **never** become `COMPLETED_WITH_ABSTENTION`. Abstention is only `RuleDecision.abstain(...)` after a successful model/retrieval path.

## Dashboard and alarms

Dashboard: `epf-sentinel-${Stage}` — started/completed, four **separate** `AnalysisStatus` series, p50/p95 latency, Gemini calls/latency/tokens, rule-abstain reasons, evidence downgrades, DLQ depth.

Alarms → `epf-sentinel-ops-alerts-${Stage}`: API 5xx, SFN failures, DLQ depth > 0, Gemini errors (incl. 429), daily Gemini calls, daily Gemini tokens. Sweep silent-stop (26h) remains from 5.1.
