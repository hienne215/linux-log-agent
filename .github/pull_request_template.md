<box border radius="lg" padding={3} gap={2}>
  **Deliverable 3**


<text color="secondary" size="xs">.github/pull_request_template.md</text>
<button block variant="outline" size="sm" onClick={() => GenUI.copy(`## Related Task
- Jira Task ID:
- Task Owner:
- Reviewer:
Description
Describe what this Pull Request changes and why.
Changes
- [ ] Added new functionality
- [ ] Fixed a bug
- [ ] Updated documentation
- [ ] Added or updated tests
- [ ] Other:
Testing
Testing steps:
1.
2.
3.
Testing results:
- [ ] Passed
- [ ] Failed
- [ ] Not applicable (explain why)
Acceptance Criteria
- [ ] All task acceptance criteria are addressed.
- [ ] Existing README content is preserved.
- [ ] Planned features are not presented as implemented.
- [ ] No unrelated files are modified.
Evidence
Attach screenshots, test outputs, or other relevant evidence.
Review Checklist
- [ ] Code/documentation has been reviewed.
- [ ] No secrets or API keys are committed.
- [ ] No unnecessary files are included.
- [ ] Changes are ready for review.
Reviewer Comments
Add review notes here.
`)}> <icon name="copy" size="xs"/> Sao chép nội dung </button>
</box>