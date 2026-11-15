# SCRUM-42 metrics

Offline recomputation only. No new inference or Linux run. Hash references are provenance, not authentication.
Unique runs: 18; exact duplicates ignored: 0.

## live_llm_mock_tools / scrum14-instruct-6x3-native-selection

Revision: `990f41f68c8ef13218bf825bd57ff233a8c82f1c` (artifact snapshot, not proof of runtime identity).
Config: `3cf967fb1c6594fa5c49f17e54aecb8eb7e80feeaf3b1e57a568777f8e00e1ec`; latency scope: model_request; runs: 18.

| Metric | Value | Denominator / coverage |
|---|---|---|
| tool_selection_accuracy | 1.000000 | 18/18; missing runs 0 |
| tool_and_exact_arguments_accuracy | 1.000000 | 18/18; missing runs 0 |
| diagnostic_correctness | NOT_EVALUABLE | 0/0; missing runs 18 |
| evidence_correctness | NOT_EVALUABLE | 0/0; missing runs 18 |
| citation_validity | NOT_EVALUABLE | 0/0; missing runs 18 |
| latency | median 3.173447; mean 4.556067 seconds | 18; missing runs 0 |
| tool_dispatch_count | NOT_EVALUABLE | 0; missing runs 18 |
| timeout_rate | 0.000000 | 0/18; missing runs 0 |
| error_rate | 0.000000 | 0/18; missing runs 0 |
| cleanup_success | NOT_EVALUABLE | 0/0; missing runs 18 |
| unauthorized_write_rejection | NOT_EVALUABLE | 0/0; missing runs 18 |
| outside_allowlist_executions | NOT_EVALUABLE | 0; missing runs 18 |
| verify_success | NOT_EVALUABLE | 0/0; missing runs 18 |

Error categories (multi-label; counts are not disjoint): {}

## Source artifacts

| SHA-256 | Record pointers |
|---|---|
| 472bef128482cc2f0491b1ebb2e68486245a0061cb2b561c15e71976e2d336ab | /rows/0, /rows/1, /rows/10, /rows/11, /rows/12, /rows/13, /rows/14, /rows/15, /rows/16, /rows/17, /rows/2, /rows/3, /rows/4, /rows/5, /rows/6, /rows/7, /rows/8, /rows/9 |

## Error runs

| Run ID | Artifact SHA-256 / record pointer | Categories |
|---|---|---|
| None observed in evaluable fields | — | No inferred failures |
