# EPF Sarthi

**Your EPFO claim is overdue. EPF Sarthi tells you exactly why, by how many days, and what to say about it.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![AWS SAM](https://img.shields.io/badge/AWS_SAM-IaC-FF9900?logo=amazon-aws&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini_3.6_Flash-AI-4285F4?logo=google&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

---

## The Problem

EPFO manages provident fund accounts for **~300 million subscribers** — roughly one in four working Indians. When a member files a Final Settlement claim (Form 19), EPFO's own Citizens' Charter mandates resolution within **20 calendar days**. In practice, claims sit pending for weeks or months. When members follow up:

- The member portal shows a status string (`PENDING`, `UNDER_PROCESS`) with no deadline, no countdown, and no explanation.
- The applicable rule — and the **specific text** that establishes the timeline — is buried in EPFO circulars, the EPF Scheme 1952, and Citizens' Charter PDFs that most members never find.
- Filing a grievance on EPFiGMS requires knowing the rule, the deadline date, and the exact legal language — information the portal does not surface.
- There is no way to know whether a claim is genuinely in process or silently stalled.

The result: members wait passively, escalate to employers or agents, or file vague grievances that get closed without resolution.

---

## The Solution

EPF Sarthi is a serverless web application that takes a member's claim details and an uploaded document, runs a five-stage AI analysis pipeline, and produces four concrete outputs within ~60 seconds:

1. **SLA verdict** — whether the published 20-day statutory deadline has been exceeded, with the exact deadline date and days elapsed computed deterministically (no AI involved in the arithmetic).
2. **Cited rule text** — the verbatim quote from the EPFO rule corpus that establishes the timeline, with a citation to the source chunk. If no applicable rule is found, the system says so explicitly rather than inventing one.
3. **Evidence checklist** — five checks against the uploaded document (KYC, bank details, date of exit, deficiency notice, amount consistency), each with a verdict (`CONFIRMED` / `NOT_FOUND` / `CONTRADICTED`) grounded in a verbatim substring from the extracted text.
4. **Draft grievance** — an EPFiGMS-ready grievance text the member can copy and paste. The draft is never submitted automatically.

**What it does not do:** log into EPFO, scrape member data, submit grievances, or assert EPFO fault — only that a published timeline appears exceeded on the member's own data.

---

## Key Features

- **Deterministic SLA engine** — deadline arithmetic runs in pure Python (`shared/sla.py`) with zero model calls; a hallucinated date is structurally impossible.
- **Grounded rule citations** — the Rules Agent retrieves top-8 chunks by cosine similarity, then code-validates that the quoted span is a verbatim substring of a cited chunk and that the timeline integer appears in the source text before accepting the output. Fabricated citations trigger an explicit abstention, not a wrong answer.
- **Document vision extraction** — uploaded PDFs and images are routed through Gemini's vision model (`gemini-3.6-flash`) when a text layer is absent; the extracted text feeds the Evidence Agent's substring checks.
- **PII redaction before model calls** — UAN (12-digit), bank account numbers (9-18 digit), and password-like fields are stripped in code in `grievance_agent.py` before any outbound Gemini request.
- **Per-user daily analysis cap** — enforced at 20 analyses/day in `start_analysis.py` via a dedicated DynamoDB quota table; returns HTTP 429 with a structured error code on breach.
- **Daily SLA sweep** — an EventBridge-triggered Lambda (`sweep_function`) re-checks all open claims nightly and can surface newly overdue claims without user action.

---

## Architecture

```
Browser (React + Vite)
        |
        |  HTTPS (Cognito JWT)
        v
API Gateway HTTP API
        |
   +----+---------------------------------------------+
   |                Lambda Functions                   |
   |  POST /claims          --> create_claim           |
   |  GET  /claims          --> list_claims            |
   |  GET  /claims/{id}     --> get_claim              |
   |  POST /documents/presign --> presign_upload       |
   |  POST /claims/{id}/analyze --> start_analysis     |
   |  GET  /runs/{id}       --> get_run                |
   |  DELETE /account       --> delete_account         |
   +----+---------------------------------------------+
        |
        |  S3 presigned PUT (document upload)
        v
   S3 Bucket (epf-sentinel-docs)
        |
        |  S3:ObjectCreated --> SQS --> extract_document
        v
   ExtractDocument Lambda
   +-- PDF: pypdf text layer
   +-- Image / PDF fallthrough: Gemini vision --> text
        |
        |  Stores extracted text in DynamoDB Documents table
        v
   start_analysis Lambda
        |
        |  StartExecution (Express Workflow)
        v
+--------------------------------------------------+
|        Step Functions Express Workflow           |
|                                                  |
|  1. ClaimAgent       <- Gemini (normalise input) |
|         |                                        |
|  2. RulesAgent  <- embed_query -> DynamoDB scan  |
|         |         -> Gemini (select rule)        |
|         |         -> code guards (grounding)     |
|         |                                        |
|  3. SlaAgent    <- pure Python arithmetic        |
|         |                                        |
|  4. EvidenceAgent <- Gemini (5 checks)           |
|         |          -> verbatim substring guard   |
|         |                                        |
|  5. GrievanceAgent <- PII redact -> Gemini draft |
|         |           -> grounding check           |
|         |                                        |
|  6. PersistRun  -> DynamoDB AnalysisRuns         |
|         |                                        |
|  [any error] -> RecordFailure -> DynamoDB        |
+--------------------------------------------------+
        |
        |  EventBridge (daily cron)
        v
   SweepFunction Lambda  (nightly SLA re-check)

Data stores (all DynamoDB, on-demand billing, no VPC):
  +-- Claims         PK: userId  SK: claimId
  +-- Documents      PK: userId  SK: documentId   (TTL 30d via S3 lifecycle)
  +-- RuleChunks     PK: ruleSetVersion  SK: chunkId  (vector store)
  +-- AnalysisRuns   PK: claimId  SK: runId  (TTL 90d)
  +-- AnalysisQuota  PK: userId  SK: date  (daily cap counter)

AI layer: Google Gemini API (generation + embedding)
  +-- shared/gemini.py -- single client, key from Secrets Manager,
      retry on 429/5xx only, cold-start cached in module scope
```

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| **Frontend** | React 19 + TypeScript + Vite | SPA; react-router-dom v7, Motion animations |
| **Frontend styling** | Tailwind CSS v4 | PostCSS pipeline |
| **Frontend hosting** | AWS Amplify Hosting + CloudFront | CI from `amplify.yml` |
| **Auth** | Amazon Cognito User Pool | JWT authorizer on HTTP API; `sub` claim partitions all data |
| **API** | Amazon API Gateway HTTP API | Stage-level throttle (dev: 100 rps, prod: 20 rps) |
| **Compute** | AWS Lambda — Python 3.12 | 19 functions; shared utilities in a Lambda Layer |
| **Orchestration** | AWS Step Functions (Express) | 5-agent sequential pipeline + error-routing states |
| **AI — generation** | Google Gemini `gemini-3.6-flash` | Text generation, vision OCR, structured JSON output |
| **AI — embedding** | Google Gemini `gemini-embedding-001` | 3072-dim vectors for rule chunk retrieval |
| **Vector store** | DynamoDB brute-force cosine | ~100-300 rule chunks; upgrade path: Aurora Serverless v2 + pgvector |
| **Database** | Amazon DynamoDB (on-demand) | 5 tables; no VPC, no RDS |
| **Object storage** | Amazon S3 | Presigned PUT, SSE-S3, 30-day lifecycle |
| **Async** | Amazon SQS + EventBridge + SNS | Doc processing queue, daily sweep, ops alerts |
| **Secrets** | AWS Secrets Manager | Gemini API key only; read at Lambda cold start, module-scope cached |
| **IaC** | AWS SAM (`infra/template.yaml`) | Single `make deploy` deploys all resources |
| **Observability** | Amazon CloudWatch | Structured JSON logs, custom metrics, dashboard, alarms to SNS |
| **Testing** | pytest + Hypothesis | Unit tests in `tests/unit/`; property-based tests for SLA engine |

---

## Getting Started

### Prerequisites

| Tool | Minimum version |
|---|---|
| Python | 3.12 |
| Node.js | 20 |
| AWS CLI | v2, configured with credentials for `ap-south-1` |
| AWS SAM CLI | 1.120+ |
| make | any |

### 1. Clone

```bash
git clone <repo-url>
cd EPF_WatchDog
```

### 2. Store the Gemini API key in Secrets Manager

> **Never** pass the key value on the CLI or commit it to source control.
> `GeminiSecretName` is the **name** of the secret, not the key value.

```bash
# Dev
aws secretsmanager create-secret \
  --name epf-sentinel/gemini-api-key \
  --secret-string '{"GEMINI_API_KEY":"YOUR_GEMINI_API_KEY_HERE"}' \
  --region ap-south-1

# Prod (must be a different secret name)
aws secretsmanager create-secret \
  --name epf-sentinel/gemini-api-key-prod \
  --secret-string '{"GEMINI_API_KEY":"YOUR_GEMINI_API_KEY_HERE"}' \
  --region ap-south-1
```

### 3. Seed the EPFO rule corpus

```bash
# Chunk, embed, and load the Citizens' Charter / Scheme 1952 text into DynamoDB RuleChunks
python scripts/seed_rules.py --stage dev
```

<!-- TODO: confirm exact script name and any required arguments -->

### 4. Deploy the backend

```bash
# Dev (uses samconfig.toml [default] section)
make deploy STAGE=dev

# Prod (set your Amplify URL; deploy frontend first to get it)
make deploy STAGE=prod AMPLIFY_ORIGIN=https://<your-app-id>.amplifyapp.com
```

The first `sam deploy` output includes the API Gateway base URL and all resource ARNs.

### 5. Configure and run the frontend locally

```bash
cd frontend
cp .env.example .env.local
```

Edit `.env.local` with the values printed by `make deploy`:

```env
VITE_API_BASE_URL=https://<api-id>.execute-api.ap-south-1.amazonaws.com
VITE_COGNITO_USER_POOL_ID=ap-south-1_XXXXXXXXX
VITE_COGNITO_CLIENT_ID=XXXXXXXXXXXXXXXXXXXXXXXXXX
VITE_AWS_REGION=ap-south-1
```

<!-- TODO: confirm .env.example exists; create it if not -->

```bash
npm install
npm run dev
# Opens at http://localhost:5173
```

### 6. Run unit tests

```bash
make test
# Equivalent to: python -m pytest tests/unit -v --tb=short
```

### Environment Variables Reference (Lambda — all set by SAM, not manually)

| Variable | Set by | Example value |
|---|---|---|
| `STAGE` | SAM parameter override | `dev` / `staging` / `prod` |
| `GEMINI_SECRET_NAME` | Makefile | `epf-sentinel/gemini-api-key` |
| `CLAIMS_TABLE_NAME` | SAM template | `epf-sentinel-Claims-dev` |
| `RULECHUNKS_TABLE_NAME` | SAM template | `epf-sentinel-RuleChunks-dev` |
| `ANALYSIS_RUNS_TABLE_NAME` | SAM template | `epf-sentinel-AnalysisRuns-dev` |
| `DOCUMENTS_TABLE_NAME` | SAM template | `epf-sentinel-Documents-dev` |
| `ANALYSIS_SFN_ARN` | SAM template | `arn:aws:states:ap-south-1:...` |
| `DAILY_ANALYSIS_CAP` | Makefile (default: 20) | `20` |
| `USE_STUBS` | Optional manual override | `false` (default); set `true` to bypass Gemini in dev |

---

## Usage

### Main user flow

**1. Sign in** — Cognito-hosted sign-in or sign-up. The API derives tenancy exclusively from the JWT `sub`; no user ID is accepted from the browser.

**2. Create a claim** — Enter claim type (Final Settlement), filing date, amount, and current status. The `claim_agent` Lambda normalises and validates these fields; Gemini resolves ambiguous free-text input.

![Claims list](docs/screenshots/claims-list.png)

**3. Upload a document** — The frontend calls `presign_upload` to get a presigned S3 PUT URL, uploads the file directly from the browser. SQS triggers `extract_document`: pypdf for text-layer PDFs, Gemini vision for images and scanned PDFs. Status polls until `EXTRACTED`.

**4. Run analysis** — Click **Run analysis**. `start_analysis` enforces the daily cap, creates an `AnalysisRun` (`RUNNING`), and fires the Step Functions Express workflow. The frontend polls `GET /runs/{runId}` every 2 seconds for up to 120 seconds.

![Analysis in progress](docs/screenshots/analysis-running.png)

**5. Review results** — Four panels appear when the run completes:

- **SLA verdict** — green (`WITHIN`) / amber (`APPROACHING`) / red (`OVERDUE`) with deadline date and exact day count.
- **Rule citation** — verbatim EPFO rule text + source document reference.
- **Evidence checklist** — five checks, each `CONFIRMED` / `NOT_FOUND` / `CONTRADICTED`.
- **Grievance draft** — starts with the mandatory disclaimer; includes claim date, amount, 20-day timeline, and deadline date. Copy-only; never auto-submitted to EPFO.

![Analysis results](docs/screenshots/analysis-result.png)

<!-- TODO: replace screenshot paths with actual paths after deploy -->

---

## API Documentation

All routes (except `/health`) require `Authorization: Bearer <cognito-jwt>`.

Base URL: printed by `make deploy` as the API Gateway stage URL.

| Method | Route | Lambda | Description |
|---|---|---|---|
| `GET` | `/health` | `health` | Health check. Returns `{"status":"ok","version":"<git-sha>","time":"<ISO8601>"}`. No auth. |
| `POST` | `/claims` | `create_claim` | Create a claim. Body: `{"claimType","claimDateIso","amountPaise","status","notes?"}` |
| `GET` | `/claims` | `list_claims` | List all claims for the authenticated user. |
| `GET` | `/claims/{claimId}` | `get_claim` | Fetch one claim with its latest analysis run. |
| `POST` | `/documents/presign` | `presign_upload` | Get presigned S3 PUT URL. Body: `{"claimId","contentType","filename"}`. Returns `{"uploadUrl","documentId"}`. |
| `GET` | `/documents/{documentId}` | `get_document` | Document metadata and extraction status. |
| `POST` | `/claims/{claimId}/analyze` | `start_analysis` | Start the pipeline. Returns `202 {"runId":"..."}`. Rate-limited: 20/user/day. |
| `GET` | `/runs/{runId}` | `get_run` | Poll for run status and results. |
| `DELETE` | `/account` | `delete_account` | Delete all data for the authenticated user (GDPR-style wipe). |

<details>
<summary>Error response schema</summary>

```json
{
  "error": {
    "code": "ANALYSIS_CAP_EXCEEDED",
    "message": "Daily analysis limit reached."
  }
}
```

Standard error codes: `INTERNAL_ERROR`, `NOT_FOUND`, `CLAIM_NOT_FOUND`, `DOCUMENT_NOT_READY`, `ANALYSIS_CAP_EXCEEDED`.

</details>

<details>
<summary>Analysis run terminal states</summary>

| `status` value | Meaning |
|---|---|
| `RUNNING` | Pipeline executing |
| `COMPLETED` | Full result available |
| `COMPLETED_WITH_ABSTENTION` | Rules Agent found no applicable rule; SLA step skipped |
| `FAILED_VALIDATION` | Input rejected before any model call |
| `FAILED_INFRASTRUCTURE` | Gemini or AWS dependency error — not an abstention |

A Gemini outage always produces `FAILED_INFRASTRUCTURE`, never `COMPLETED_WITH_ABSTENTION`. The distinction is enforced in code.

</details>

---

## Impact

EPFO administers provident fund accounts for an estimated **~300 million active subscribers** as of 2024. In FY 2023-24, EPFO settled roughly **30 million claims**. Delays in Final Settlement directly affect members at retirement, job change, or financial emergency — the moments when they most need the money.

EPF Sarthi addresses the information asymmetry: members are entitled to a statutory 20-day settlement timeline under the EPF Scheme 1952 and the Citizens' Charter, but have no tool today to:

- Know exactly when that deadline falls given their specific claim date.
- Read the rule text that creates the entitlement.
- Draft a structured grievance that cites the rule and the breach.

The system is explicitly informational. Every output is prefaced with a disclaimer and requires the member to review and act — EPF Sarthi never files anything.

---

## Future Roadmap

1. **Holiday-aware SLA calendar** — `shared/sla.py` already accepts a `holidays: frozenset` parameter; the v1 deploy passes an empty set. Integrating the official EPFO/state government holiday calendar makes working-day timelines accurate.
2. **Additional claim types** — transfer claims (Form 13), advances (Form 31), pension (Form 10D). The Step Functions pipeline is claim-type-agnostic; each type only needs new rule chunks and `ClaimType` enum values.
3. **Vector store upgrade** — replace DynamoDB brute-force cosine scan with Aurora Serverless v2 + pgvector for sub-millisecond retrieval at corpus sizes beyond a few hundred chunks.
4. **SNS member notifications** — the EventBridge nightly sweep already detects newly overdue claims; wiring SNS email/SMS alerts removes the need for the member to log in to discover a breach.
5. **Multi-language grievance output** — drafts in Hindi and regional languages; the Gemini generation call in `grievance_agent.py` is the only component that changes.

---

## Contributing

1. Fork the repo and create a feature branch.
2. Run `make test` before opening a PR — all tests must pass.
3. New Lambda functions require a unit test in `tests/unit/`.
4. Do not commit secrets, real UAN numbers, real bank account numbers, or real PII in any form — including in test fixtures.
5. PRs that add new model calls must document the prompt, retry policy, and the failure path (what happens when the model is unavailable).

---

## Security

- The Gemini API key lives exclusively in AWS Secrets Manager. It is never in source code, SAM parameters, environment variable files, or Lambda logs. `gemini.py` logs the secret **name** and region on load; never the value.
- Claim text and extracted document text are sent to the Google Gemini API (a third-party model provider) after code-level PII redaction. UAN, bank account numbers, and password-like fields are stripped before any outbound call.
- JWT `sub` is the only accepted tenant identifier. The API never reads a `userId` from request bodies or query strings.
- HTTP error bodies use structured codes (`internal_error()`, `too_many_requests()`) and never echo exception text from upstream services.

---

## License

MIT — see [LICENSE](LICENSE) for details.

<!-- TODO: add LICENSE file if not present in repo -->

---

## Team

<!-- TODO: add team member names and roles -->

---

> **Disclaimer:** Output is informational, not legal advice. Every timeline claim cites a retrieved rule chunk or states that no applicable rule was found. The system never asserts EPFO is at fault — only that a published timeline appears exceeded on the user's own data.
