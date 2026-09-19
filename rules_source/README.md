# Rules Source Directory

Place EPFO rule Markdown files here for ingestion by `scripts/ingest_rules.py`.

## Required Frontmatter Format

Every `.md` file must begin with YAML frontmatter between `---` delimiters. Files missing any required field will be **refused** (not silently skipped).

```yaml
---
source_url: https://epfindia.gov.in/site_docs/PDFs/Circulars/Y2024-25/CC_English.pdf
source_title: "EPFO Citizen's Charter 2024-25"
retrieved_on: "2024-10-01"
authority: EPFO_OFFICIAL
rule_set_version: v1
---
```

### Required Fields

| Field | Description | Example |
|-------|-------------|---------|
| `source_url` | URL where the document was retrieved | `https://epfindia.gov.in/...` |
| `retrieved_on` | Date the document was retrieved (ISO format) | `"2024-10-01"` |
| `authority` | Source authority type | `EPFO_OFFICIAL` or `SCHEME_TEXT` |

### Optional Fields

| Field | Description | Default |
|-------|-------------|---------|
| `source_title` | Human-readable document title | Filename stem |
| `rule_set_version` | Version string for this rule set | CLI `--rule-set-version` arg |

## Authority Values

- **`EPFO_OFFICIAL`** — EPFO-published material (Citizen's Charter, circulars, official guidelines)
- **`SCHEME_TEXT`** — EPF Scheme 1952 paragraphs, statutory text

## Sample File

```markdown
---
source_url: https://epfindia.gov.in/site_docs/PDFs/Circulars/Y2024-25/CC_English.pdf
source_title: "EPFO Citizen's Charter 2024-25"
retrieved_on: "2024-10-01"
authority: EPFO_OFFICIAL
rule_set_version: v1
---

## Claim Settlement Timelines

### Final Settlement (Form 19)

As per the EPFO Citizen's Charter, final settlement claims (Form 19)
shall be settled within 20 days from the date of receipt of the claim
in the concerned EPFO office, provided the claim is complete in all
respects.

### Transfer Claims (Form 13)

Transfer of PF accumulations from one establishment to another shall
be completed within 20 days from the date of receipt of the claim
in the concerned EPFO office.
```

## Running Ingestion

```bash
# Set the Gemini API key secret name (must exist in Secrets Manager)
set GEMINI_SECRET_NAME=epf-sentinel/gemini-api-key

# Run ingestion
python scripts/ingest_rules.py --rules-dir rules_source --table-name epf-sentinel-RuleChunks-dev
```

## Contract Note for Downstream Modules (3.1 & 4.2)
Note: `charterTargetDays` (Optional[int], populated only when model-extracted and grounded) and `CONFLICTING_TIMELINE_SOURCES` (as a fifth `RuleAbstain` reason) are formalized contract additions beyond the original Module 2.2 specification, providing dual statutory/charter tracking and explicit multi-timeline collision handling for downstream consumption.

