# Evidence

Screenshots and logs collected while setting up and running the system on 8 November 2026.
Terminal logs live in `../logs/` (`experiment_*.log` are full runs; the complete six-prompt
run is `experiment_20261108_165921.log`; `audit_log.jsonl` is one JSON record per query).
Similarity figures are `../similar_houses_query-house-1.png` and
`../similar_houses_query-house-2.png`. Written observations per agent are in `../evaluation.md`.

## Screenshots (`screenshots/`)

| # | File | What it shows |
|---|------|---------------|
| 01 | `01-foundry-inventory-embedding-missing.png` | `create_deployments.py --list`: AI Foundry account `ethicalassignment` has `gpt-4.1-mini` but no embedding deployment |
| 02 | `02-foundry-create-embedding-deployment.png` | Same script creating `text-embedding-ada-002` (Standard, 50K TPM) and reporting Succeeded |
| 03 | `03-inspect-openai-both-deployments-ok.png` | `inspect_openai.py`: both deployments answer; embedding returns 1536 dims |
| 04 | `04-keyvault-verify-11-secrets.png` | `verify_keyvault.py`: all 11 secrets readable from vault `structuredazureendpoint` |
| 05 | `05-run-experiment-secrets-and-agent-load.png` | `run_experiment.py`: cached login reused, secrets verified, structured agent loading |
| 06 | `06-chat-start-device-code-login.png` | `python chat.py` start: device-code prompt and banner |
| 07 | `07-chat-agents-loaded-chroma-index-clip-download.png` | All three agents loaded: 21 Mongo documents embedded into Chroma, CLIP model downloaded |
| 08 | `08-unstructured-presidio-pii-sources-scan-1.png` | Unstructured agent: Presidio entities (PERSON, LOCATION, DATE_TIME, NRP, EMAIL_ADDRESS) with confidence scores, values masked |
| 09 | `09-unstructured-presidio-pii-sources-scan-2-audit.png` | Second scan, "No PII detected in generated answer", audit record written |
| 10 | `10-structured-fairlearn-education-level-crosstabs.png` | Structured agent fairness audit: `education_survey` by `education_level`, categorical cross-tabs |
| 11 | `11-structured-fairlearn-income-disparity-by-education.png` | `annual_income` by `education_level`: mean ratio 0.190, "Large disparity detected" |
| 12 | `12-structured-fairlearn-gender-crosstabs.png` | `education_survey` by `gender`: neighborhood and education cross-tabs |
| 13 | `13-structured-fairlearn-gender-numeric-means.png` | Age, experience, income by gender: ratios 0.93 to 0.99, no disparity |
| 14 | `14-structured-fairlearn-race-houses-neighborhood.png` | `neighborhood_houses` by `identified_race`: neighborhood distribution |
| 15 | `15-structured-fairlearn-race-income-size-value.png` | Household income and house size by race: ratios 0.949 and 0.986 |
| 16 | `16-structured-fairlearn-race-binary-pool-ownership.png` | Fairlearn binary checks: swimming pool parity difference 0.0294, ownership 0.0499, both within threshold |
| 17 | `17-structured-fairlearn-schools-average-age-crosstab.png` | `neighborhood_schools` audited by `average_age` (false positive, see evaluation.md) |
| 18 | `18-structured-fairlearn-schools-student-counts.png` | Student counts by `average_age` group, flagged as disparity |
| 19 | `19-structured-fairlearn-schools-race-counts-1.png` | Race-count columns by `average_age` group |
| 20 | `20-structured-fairlearn-schools-race-counts-2.png` | Race-count columns continued, all flagged |
| 21 | `21-structured-answer-with-fairness-advisory-audit.png` | Audit record and the final answer with the FAIRNESS ADVISORY prepended |
| 22 | `22-unstructured-restaurant-permit-pii-sources.png` | Restaurant permit query: Presidio entities found in source permits, including an email address at confidence 1.00 |
| 23 | `23-unstructured-answer-redacted-multimodal-query-screened.png` | PII (LOCATION, PERSON) detected in the generated answer and redacted to `[LOCATION]`, `[PERSON]`; then the multimodal query image screened by Content Safety (all categories 0) with an empty `houses` container, Blob count 0 |

## Reproducing

```bash
cd starter && source .venv/bin/activate
python run_experiment.py --skip-load      # six prompts, two per agent, log in logs/
python chat.py                            # interactive session
```
