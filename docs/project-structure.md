# Repository Structure and Git Workflow

## 1. Project Information

- Project: Developing an AI Agent for Intelligent Linux Log Analysis and Troubleshooting
- Repository: linux-log-agent
- Main branch: main
- Task tracking: Jira
- Version control: Git and GitHub

## 2. Proposed Repository Structure

```
linux-log-agent/
├── README.md
├── docs/
│   ├── project-structure.md
│   ├── documentation-matrix.md
│   └── decisions/
├── .github/
│   └── pull_request_template.md
├── src/
│   ├── mcp_server/
│   └── agent/
├── tests/
├── scripts/
└── .gitignore
```

The structure is a proposal. Directories will be created when their implementation starts.

## 3. Branch Naming Convention

Format: <type>/<Task-ID>-<description>

Examples:
- task/P00-01-repo-pr-standardization
- feature/P01-01-query-journal
- fix/P01-02-journal-filter
- docs/P00-01-pr-guide

Rules:
- Do not commit directly to main.
- Use a separate branch for each task.
- Do not share working branches between owners.
- Merge changes through Pull Requests.

## 4. Commit Convention

Format: <type>(<Task-ID>): <description>

Types:
- feat: New functionality
- fix: Bug fixes
- docs: Documentation
- test: Tests
- refactor: Code restructuring
- chore: Maintenance

Example:
docs(P00-01): add repository conventions

## 5. Pull Request Rules

Every Pull Request must:
1. Reference its Jira Task ID.
2. Describe the purpose of the changes.
3. List modified files.
4. Include testing steps and results.
5. Include evidence when applicable.
6. Have at least one reviewer other than the owner.

The PR author must address review comments before merging.

## 6. README Preservation

The existing README.md must be preserved.
Do not delete or overwrite its existing content.
Any future README changes must be reviewed and approved.

## 7. Documentation Status

Documentation must distinguish:
- Implemented: Developed and verified.
- In Progress: Currently being developed.
- Planned: Proposed or scheduled, not yet implemented.

Planned features must not be described as completed functionality.
