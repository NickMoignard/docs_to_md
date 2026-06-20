## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues in `NickMoignard/docs_to_md`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default triage vocabulary — `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Releasing to public master

`master` is a clean public subset of `development`, published via the `/publish-to-master`
skill (never a branch merge). See `docs/agents/release.md`.
