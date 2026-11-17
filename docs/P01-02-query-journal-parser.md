# SCRUM-16 / P01-02: independent query_journal parser draft

Owner: Thủy. Reviewer: Trang. This draft covers parsing only, not the complete
query_journal/search_log ticket. Base is PR #14 at
656f8c365a300270418e15b34ae1046d12fbcb44, including PR #6 at
cefd523d41ee71b7e76e385efa466f9aaa6c5188. PR #22 is not included in this diff.

## Implemented boundary

`src/mcp_server/journal_parser.py` accepts a bounded bytes snapshot of journalctl's
JSON-lines representation. The format is described by the official
[journalctl manual](https://www.freedesktop.org/software/systemd/man/247/journalctl.html).
No journalctl command, worker, CLI flag or scaffold registration is implemented.

- Required scalar fields: __REALTIME_TIMESTAMP (ASCII decimal microseconds since
  Unix epoch), PRIORITY (string 0..7), MESSAGE (Unicode string <=2048 characters).
  Timestamp conversion uses integer microseconds and emits UTC RFC3339 with six
  fractional digits. No boot-time inference, clock correction or invented priority.
- Output is a list of canonical entry-shaped timestamp/priority/message mappings,
  preserving input order. Unrelated metadata is ignored, never forwarded. No
  envelope, status, evidence, correlation IDs, count/limit policy or cursor is built.
- Invalid UTF-8, unpaired surrogates, malformed JSON, duplicate keys, non-object
  records, missing fields, non-finite JSON values and unrepresentable timestamps
  reject the whole batch with a static local exception. No partial records returned.
  Binary/null/multi-valued representations of required fields are unsupported and
  rejected; legitimate journal output may contain these representations.
- Internal parsing bounds: 65536 input bytes, 16384 bytes per JSON record, 200
  records. Optional final newline/CRLF accepted; interior blank lines rejected.
  Oversize rejects without slicing; these are parser allocation bounds, not MCP
  OUTPUT_LIMIT/partial semantics or proof that an envelope fits the transport cap.
- MESSAGE remains raw UNTRUSTED text, including control characters or secrets.
  Parsing is not redaction. Never publish the return value until the separately
  reviewed privacy/evidence pipeline exists. Empty input yields [] only; it does
  not prove journal access, coverage, or absence of events. No synthetic fallback.

## Pending gates (not decided by this draft)

P03-01 / PR #13 at f914a3615356b7944cae3912e670b255df096927 provides a proposed
permission matrix, runbook and VM probe evidence. It is not a finalized permission
or redaction policy: without systemd-journal, the reader saw only its own journal;
group membership exposes the whole journal, so unit filtering alone is insufficient.

Trang must confirm reader/access policy, application unit allowlist and redaction
fixtures/policy before a real backend can expose parsed messages or evidence.
Empty-versus-denied handling, incomplete history/rotation and coverage mapping need
review. Canonical request/result schemas already require pagination, but real
journal cursor continuation/lifetime/ownership and rotation behavior remain to be
designed/reviewed without borrowing synthetic offset semantics silently.

Fixed argv/subprocess, deadlines and cancellation/cleanup, canonical request/time
filter enforcement, evidence provenance, error/partial/output-cap mapping and SDK
integration are future collector work. No real Linux smoke was performed.

## Targeted verification

Python 3.12, synthetic in-memory fixtures only:

```text
python -m pytest -q tests/test_journal_parser.py --basetemp=<writable-temp-directory>
```

Tests validate canonical data mapping with FormatChecker, valid/missing/malformed
fields, timestamp precision/range, every priority, UTF-8, unsupported binary or
multi-valued fields, size/count boundaries, atomic batch rejection and safe errors.

Local verification: Windows, Python 3.12.7; targeted parser suite **62 PASS,
0 FAIL, 0 ERROR, 0 SKIP**. These are synthetic parser tests, not Linux smoke,
SDK collector integration or independent reviewer signoff.
