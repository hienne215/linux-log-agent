# Repository Structure and Git Workflow

## 1. Project Information

- Project: Developing an AI Agent for Intelligent Linux Log Analysis and Troubleshooting
- Repository: `linux-log-agent`
- Main branch: `main`
- Task tracking: Jira
- Version control: Git and GitHub
- Source of scope: [`docs/proposal-v2.md`](proposal-v2.md) (agreed project outline)

## 2. Proposed Repository Structure

```
linux-log-agent/
├── README.md                      # existing file, preserved
├── requirements.txt
├── .gitignore
├── .github/
│   └── pull_request_template.md
├── docs/
│   ├── project-structure.md
│   ├── documentation-matrix.md
│   ├── proposal-v2.md
│   ├── mcp-tool-contracts.md      # tool schemas (A and B)
│   ├── architecture.md
│   ├── security-design.md         # (C)
│   ├── evaluation-scenarios.md    # scenarios, answer keys (C)
│   ├── setup-guide.md             # VM and install steps (C)
│   ├── test-report.md
│   ├── decisions/                 # short decision notes
│   └── report/                    # final report and slides (English)
├── src/
│   ├── mcp_server/                # MCP server and tools (A)
│   ├── agent/                     # MCP client, agent loop, CLI (B)
│   └── helper/                    # privileged helper for write actions (C)
├── eval/                          # fault scripts, answer keys, results (C)
├── scripts/                       # environment and install scripts
└── tests/
```

Ownership: A = `src/mcp_server/`, B = `src/agent/`, C = `src/helper/`, `eval/`, `scripts/`.
Shared folders (`docs/`, `tests/`) are edited by whoever owns the related task.

This structure is a proposal. Create a directory only when its implementation starts.
Git does not track empty directories; add an empty `.gitkeep` file if a folder must exist early.

## 3. Branch Naming Convention

Format: `<type>/<Task-ID>-<short-description>`

Allowed types: `feature`, `fix`, `docs`, `test`, `chore`

Examples:
- `docs/P00-01-repo-pr-standardization`
- `feature/P01-01-query-journal`
- `fix/P01-02-journal-filter`

Rules:
- Do not commit directly to `main`.
- Use one branch per task, and keep branches short-lived (merge within a few days).
- Do not work on another member's branch unless both agree first (for example, when pairing).
- Merge only through Pull Requests.
- If a change has no Jira task, create the task first.

## 4. Commit Convention

Format: `<type>(<Task-ID>): <description>`

Types:
- `feat`: new functionality
- `fix`: bug fix
- `docs`: documentation
- `test`: tests
- `refactor`: code restructuring
- `chore`: maintenance

Example: `docs(P00-01): add repository conventions`

Write the description in the imperative mood and keep it under about 72 characters.

## 5. Pull Request Rules

Every Pull Request must:
1. Reference its Jira Task ID.
2. Describe the purpose of the change.
3. List the files changed.
4. State whether tool schemas or security-related settings change (see the PR template).
5. Include testing steps and results, run on the VM with real logs when applicable.
6. Include evidence (screenshot or output) when applicable.
7. Be reviewed by at least one member who is not the author.

Working agreements:
- Keep PRs small and focused on one task.
- Reviewers respond within 24 hours.
- The author addresses all review comments before merging.
- Use **Squash and merge**, then delete the branch.
- Before opening a PR, sync with main: `git pull --rebase origin main`.
- The author merges after approval.

## 6. README Preservation

- The existing `README.md` must be preserved. Do not delete or overwrite its content.
- Before opening a PR, check that README is untouched: `git diff main -- README.md` should print nothing.
- Any README change (for example, adding links to `docs/`) goes in its own PR and needs review.

## 7. Documentation Status

Every document and feature uses the same status set:

| Status | Meaning |
|---|---|
| Existing | Already in the repository before this workflow |
| Proposed | Suggested, waiting for team review |
| Planned | Agreed in the plan, not started |
| In Progress | Being worked on |
| Implemented | Completed and verified by a reviewer who is not the author |

Planned or Proposed items must never be described as completed functionality.
Statuses are tracked in [`documentation-matrix.md`](documentation-matrix.md); update it in the same PR that changes a status.
