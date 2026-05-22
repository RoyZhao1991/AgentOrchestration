# Release Provenance

The `Release` workflow runs for `v*` tags, builds the Python package
distributions with `uv build`, verifies that the tag matches the
`pyproject.toml` version, creates a repository source archive, and
publishes the same files that it attests:

- `dist/*.whl`
- `dist/*.tar.gz` package and repository source archives
- `dist/SHA256SUMS`

GitHub Artifact Attestations are generated with the workflow identity.
Each release artifact has a matching provenance attestation whose
statement binds the artifact digest to this source repository, the tag
commit, the release workflow, and the build environment used by GitHub
Actions.

## Publishing

1. Update the project version and merge the release commit to `main`.
2. Create and push a version tag, for example `v2.4.1`.
3. Wait for the `Release` workflow to finish.
4. Confirm the GitHub release contains the wheel, package source
   distribution, repository source archive, and `SHA256SUMS` assets.

## Verification

Install the GitHub CLI and verify each downloaded release asset before
installing it:

```bash
gh release download v2.4.1 \
  --repo orchestration-agent/AgentOrchestration \
  --dir dist

gh attestation verify dist/agent_orchestrator-2.4.1-py3-none-any.whl \
  --repo orchestration-agent/AgentOrchestration \
  --source-ref refs/tags/v2.4.1

gh attestation verify dist/agent_orchestrator-2.4.1.tar.gz \
  --repo orchestration-agent/AgentOrchestration \
  --source-ref refs/tags/v2.4.1

gh attestation verify dist/AgentOrchestration-v2.4.1.tar.gz \
  --repo orchestration-agent/AgentOrchestration \
  --source-ref refs/tags/v2.4.1

gh attestation verify dist/SHA256SUMS \
  --repo orchestration-agent/AgentOrchestration \
  --source-ref refs/tags/v2.4.1
```

For audit logs, capture the JSON form and retain the `subject` digest,
source repository, commit, and workflow values:

```bash
gh attestation verify dist/agent_orchestrator-2.4.1-py3-none-any.whl \
  --repo orchestration-agent/AgentOrchestration \
  --source-ref refs/tags/v2.4.1 \
  --format json > provenance-wheel.json
```

Only use artifacts whose attestation verifies for this repository and
tag. A digest mismatch, missing workflow identity, or unexpected source
commit means the artifact should be treated as untrusted.
