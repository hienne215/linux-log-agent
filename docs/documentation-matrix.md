<box border radius="lg" padding={3} gap={2}>
  **Deliverable 2**


<text color="secondary" size="xs">docs/documentation-matrix.md</text>
<button block variant="outline" size="sm" onClick={() => GenUI.copy(`# Documentation Matrix
1. Purpose
This matrix defines where project documents are stored and how their implementation status is reported.
2. Documentation Matrix
Document	Location	Owner	Status
Project overview	README.md	Team	Existing
Project structure	docs/project-structure.md	A	Proposed
Documentation matrix	docs/documentation-matrix.md	A	Proposed
PR template	.github/pull_request_template.md	A	Proposed
MCP tool contracts	docs/	A and B	Planned
System architecture	docs/	Team	Planned
Security design	docs/	C	Planned
Test report	docs/	Team	Planned
Final report	docs/	Team	Planned

3. Documentation Status Definitions
- Existing: Already present in the repository.
- Proposed: Suggested but awaiting team review.
- Planned: Included in the project plan but not implemented.
- In Progress: Currently being prepared.
- Implemented: Completed and verified.
- Approved: Reviewed and accepted by the responsible reviewer.
4. Documentation Rules
1. Keep project documents in the docs directory.
2. Keep the original README content.
3. Do not report planned features as implemented.
4. Update document status when work progresses.
5. Review important documents before marking them Approved.
5. Source of Truth
- Jira: Task status, owner, priority and progress.
- GitHub: Source code, documents, branches and Pull Requests.
- Project proposal: Agreed scope, architecture and deliverables.
  `)}> <icon name="copy" size="xs"/> Sao chép nội dung </button>
  </box>