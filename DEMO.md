# EPF Sarthi — three-minute demo

This script uses the idempotent production fixture created by:

```bash
DEMO_USER_PASSWORD='<strong password>' python scripts/seed_demo.py --stage prod
```

Never put the password in shell history, screenshots, source control, or the
evidence bundle. The fixture is synthetic and contains no real member data.

## Before the clock starts

1. Confirm `epf-sentinel-prod` is `UPDATE_COMPLETE`.
2. Confirm the seed report exists at `evidence/module-5.3/seed-prod.json`.
3. Open the production frontend in a signed-out browser.
4. Have the demo email `demo@epf-sentinel.example` ready in a password manager.
5. Confirm the seeded claim has no prior failed or running analysis. If it has
   a completed analysis, that stored result is intentionally reused.

## 0:00–0:25 — Sign in

1. On **Sign in**, enter the demo email and password.
2. Select **Sign in**.
3. Expected destination: **Your claims**.

Say: “Cognito authenticates the member. The API derives tenancy only from the
JWT subject; no user ID is accepted from the browser.”

Mismatch check: remaining on Sign in or seeing “session expired” means the demo
credentials or production frontend configuration is wrong. Stop rather than
improvising.

## 0:25–0:50 — Open the persisted claim

1. Find **Final settlement**.
2. Confirm the row says:
   - **Filed 1 Aug 2026**
   - **₹4,80,000**
   - **Not analysed** before the first run, or **Analysed** after rehearsal
3. Open the row.

Say: “The claim and its prior result are stored. Reopening it does not silently
run or bill another analysis.”

Mismatch check: a different date, amount, or duplicate demo claim means the
seed was not idempotent.

## 0:50–1:15 — Confirm claim and document

On the claim page confirm:

- Type: **Final settlement**
- Claim date: **1 Aug 2026**
- Amount: **₹4,80,000**
- Status: **Pending**
- Notes: **EPF_SENTINEL_DEMO_V1**

The uploaded screenshot is a synthetic Form 19 claim-status fixture. It says
`DEMO USER`, `DEMO-CLAIM-001`, `01-AUG-2026`, `INR 4,80,000`, and `PENDING`.
It contains no UAN, bank account, credentials, or real PII.

## 1:15–2:15 — Run or reveal analysis

1. If the page has no stored result, select **Run analysis** once.
2. While **Analysing** is shown, explain that the Express Step Functions
   workflow runs Claim, Rules, SLA, Evidence, and Grievance agents.
3. If the claim is already marked **Analysed**, use the stored result.

Expected rule result:

- **20 calendar days**
- Separate Citizens’ Charter target: **7 days**
- Verified quote: **“Settlement Time as per Scheme is 20 Days.”**
- Source: **EPFO Citizens’ Charter**

Expected SLA on **20 Sep 2026**:

- Clock started: **1 Aug 2026**
- Deadline: **21 Aug 2026**
- Days elapsed: **50**
- Days remaining: **-30 (past due)**
- Status: **Published timeline appears exceeded**

These elapsed/remaining values move with the calendar. If rehearsing after
20 Sep 2026, compute the current exact values before the demo; the deadline and
20-day statutory timeline do not change.

Mismatch checks:

- `7 working days` shown as the operative deadline is a correctness failure.
- “No applicable rule” is an abstention, not a green result.
- “Analysis could not be completed” is infrastructure failure, not an
  abstention; check Gemini quota and CloudWatch before retrying.

## 2:15–3:00 — Evidence and grievance

1. Point out the five evidence checks. `NOT_FOUND` and `CONTRADICTED` are
   intentionally different.
2. Open the grievance draft.
3. Confirm its first line is exactly:

   `DISCLAIMER: This is a user-reviewable draft generated from user-supplied data and is not legal advice.`

4. Confirm it includes the claim date, ₹4,80,000 amount, 20-calendar-day
   timeline, 21 Aug 2026 deadline, and cited source.
5. Select **Copy draft** only if needed; do not submit it to EPFO.

Say: “The system drafts; the member reviews and submits. EPF Sarthi never
files a grievance or changes an EPFO claim automatically.”

## Recovery notes

- Gemini 429: show the saved `FAILED_INFRASTRUCTURE` state and use the captured
  Module 5.2 evidence. Do not call it an abstention.
- Document still processing: wait for extraction; do not begin analysis with a
  `PROCESSING` document.
- Browser session expired: sign in again; do not create another demo user.
- Seed mismatch: rerun `seed_demo.py`; it reuses the marked claim and document
  rather than creating duplicates.
