## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues in `NickMoignard/docs_to_okf`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default triage vocabulary — `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Releasing to public master

`master` is a clean public subset of `development`, published via the `/publish-to-master`
skill (never a branch merge). See `docs/agents/release.md`.

<!-- docs-to-okf:rg-policy start -->
## Searching the codebase

- Use ripgrep (`rg`) for all searches. **Do not use `grep`** — `rg` is faster and respects ignore files.
- Mirrored Tool docs under `docs/tools/` are hidden from `rg` by default (via `.rgignore`) to keep code searches low-noise. The searchable navigation layer stays visible: each directory's `index.md`, each Tool's `glossary.md` and `log.md`, and the `docs/tools/index.md` bundles index.
- To search *inside* the mirrored docs, add `--no-ignore-dot`, e.g. `rg --no-ignore-dot "webhook signing" docs/tools/`. (Reveals the docs while still skipping `node_modules/`, `.venv/`, etc.)
<!-- docs-to-okf:rg-policy end -->
