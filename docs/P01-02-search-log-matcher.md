# SCRUM-16 / P01-02: independent search_log literal matcher

Owner: Thủy (ThuThuy06). Jira: https://vku-team-etxjrjs2.atlassian.net/browse/SCRUM-16

## Audit and selection (2026-11-17)

Actual Jira assigns this Critical task to Thủy, review to Trang. It contains
query_journal and search_log and depends on P01-01 and P03-01 (still In Review).
PR #23 implements only the independent journal parser and explicitly excludes
search_log. Repository/branch and GitHub PR searches found no real search_log
implementation. Existing mock search_log synthesizes fixtures; it is not a reader.

PR #6/#14 already contain contracts/scaffold awaiting review. PR #22/#25 contain
service_status/list_failed_units, #24 disk_usage and #26 listening_ports; these
are not duplicated. P01-07 registry and P04 integration/security gates depend on
unfinished collectors and acceptance. check_config remains BLOCKED, unchanged at
8b8cfca39644052d6169131cbcd9871327c9a5b7; no verifier is enabled.

## Functional increment

`match_literal_indices(messages, query, *, limit=100)` accepts a bounded built-in
list/tuple of strings and returns a tuple of matching snapshot indices in input
order. Duplicates are preserved. Matching is literal, case-sensitive Unicode;
there is no regex compilation, normalization, source access or filesystem path
interpretation. Regex syntax, traversal strings and shell syntax are plain text.
These are internal primitive semantics, not an approved endpoint query policy.

Bounds: 200 input records, 2048 characters per message, 500 query characters,
65536 total UTF-8 bytes including the query, result limit 1–200. Invalid types,
booleans as limits, empty queries and unpaired surrogates are rejected. Input is
fully validated before matching. Batch or result overflow raises a static typed
LogSearchError; no partial result, silent truncation or fabricated cursor. Work
and transient allocation are bounded by these caps; no hard-wall deadline claimed.

Messages may contain sensitive or control text. This is NOT redaction or safe
display validation. The function never returns message/query text, writes logs
or constructs exceptions containing input. Indices preserve snapshot order only;
they are not evidence IDs, source verification or permission to forward raw logs.
No MCP registration/import connects this primitive to a response or LLM.

## Dependency base and validation

Base: scaffold #14 at 656f8c365a300270418e15b34ae1046d12fbcb44, containing
canonical #6 dependency cefd523d41ee71b7e76e385efa466f9aaa6c5188.
The search_log data schema at #6 dc53ff6c3afd038f66e987bf1c7f7ad17f4b7829
is unchanged from this base. No PR #22 or #23 changes are included.

Run on Windows / Python 3.12 using synthetic in-memory fixtures:

```text
python -m pytest -q tests/test_log_search_matcher.py --basetemp=<writable-temp>
git diff --check
```

Tests check literal/Unicode behavior, duplicates, regex-shaped patterns, limits,
whole-batch rejection, invalid iterators/types, static errors and no printed text.
One test projects synthetic entries using returned indices and validates against
the canonical data schema with FormatChecker. The production API returns indices,
not canonical response data. No live Linux smoke or MCP integration is claimed.

Fresh local result: 47 PASS, 0 FAIL, 0 ERROR, 0 SKIP on Windows / Python 3.12.7.
Canonical fixture projection and `git diff --check` passed. This is author-run
offline validation, not independent signoff or full-ticket acceptance.

## Remaining gates

This does not complete SCRUM-16. No reader, subprocess, source/path allowlist,
timestamp/window/unit/severity filtering, rotation handling, permission detection,
redaction, coverage, evidence, cursor, MCP envelope/transport or Linux smoke is
implemented. These need the final P03-01 reader/privacy policy, contract decisions
and independent review by Trang. Do not expose this primitive directly as a tool
or treat an empty match tuple as proof of source availability/history coverage.
Future integration must define query semantics and overflow/pagination behavior,
preserve provenance, and apply approved privacy controls before publication.
