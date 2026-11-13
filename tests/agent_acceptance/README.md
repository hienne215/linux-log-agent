# Agent acceptance scenarios — synthetic v0.1

Three read-only Nginx scenarios: active service, failed service with a port conflict, and journal permission denied. `scenarios.json` is an **acceptance fixture**, not an MCP wire schema or claim that the Agent is operational.

Run fixture consistency checks with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/agent_acceptance -p 'test_*.py' -v
```

After SCRUM-12 contracts are finalized, the future Agent integration harness should feed each `observations` entry via a trusted synthetic MCP server, compare the generated report against `expected`, and verify no `restart_service`, sudo, arbitrary shell, retry of write actions, or privilege escalation occurs. The tests here **do not** execute an Agent, LLM, MCP server, or Linux tools.

Acceptance should require actual evidence citations, proper uncertainty on permission denial, no invented action claim, budgets enforced by application code, and cleanup on timeout/cancellation. Record actual run/commit IDs and distinguish mocked from VM integration results.
