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

<!-- docs-to-md:rg-policy start -->
## Searching the codebase

- Use ripgrep (`rg`) for all searches. **Do not use `grep`** — `rg` is faster and respects ignore files.
- Mirrored Tool docs under `docs/tools/` are hidden from `rg` by default (via `.rgignore`) to keep code searches low-noise. The navigation layer stays searchable: `docs/tools/CONTEXT-MAP.md`, and each Tool's `_index.md` and `CONTEXT.md`.
- To search *inside* the mirrored docs, add `--no-ignore-dot`, e.g. `rg --no-ignore-dot "webhook signing" docs/tools/`. (Reveals the docs while still skipping `node_modules/`, `.venv/`, etc.)
<!-- docs-to-md:rg-policy end -->
