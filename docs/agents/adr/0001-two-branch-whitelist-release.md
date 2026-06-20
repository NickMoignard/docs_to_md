# Two-branch release: fat `development`, clean `master` published by whitelist

This repo is developed with AI-agent scaffolding (the `.sandcastle/` harness,
`package.json`/`node_modules`, `AGENTS.md`, `docs/agents/`, `CLAUDE.md`, and in-repo
skills) that we want kept under version control for ongoing development, but that we do
**not** want people to clone when they install the skill. So the public surface and the
development surface are split across two branches:

- **`development`** — the full superset; the branch agents and maintainers work on.
- **`master`** — the public default branch; a strict *subset* containing only the
  shipped skill (`SKILL.md` + `crawl.py`), public docs (`README.md`, `CONTEXT.md`,
  `docs/adr/`), `tests/`, packaging (`pyproject.toml`, `uv.lock`, `.tool-versions`,
  `.gitignore`), and `LICENSE`.

`master` is produced from `development` by **copying a fixed whitelist of paths**, never
by a branch merge. The `/publish-to-master` skill is the single source of truth for that
whitelist and performs the copy.

## Considered Options

- **Whitelist publish skill** (chosen) — master is rebuilt from an explicit allow-list of
  paths. A new scaffolding file can never leak, because only listed paths are ever copied.
  Master accumulates ordinary "Publish: …" commits over time. Costs: master history is
  synthetic (not real merges), and the whitelist must be extended when a genuinely public
  file is added.
- **Cherry-pick source-only commits** — keep scaffolding and source changes in separate
  commits and cherry-pick the source ones onto master. Preserves real history but is
  fragile: a single mixed commit leaks scaffolding, and it demands constant commit
  discipline.
- **Blocklist / filter on merge** — `git merge` then strip scaffolding. Inverts the safe
  default: you must remember to block every new dev-only file; forgetting one leaks it.
- **Two separate repositories** (private superset + public mirror) — strongest isolation,
  but doubles the remotes and the sync tooling for a single small skill.

## Consequences

Master is guaranteed to be a strict subset of development as long as everything public
flows through the whitelist. When you add a new **public** file (one users should get),
you must add it to the whitelist in `.claude/skills/publish-to-master/SKILL.md` —
otherwise it silently never ships. Dev-workflow ADRs live here in `docs/agents/adr/`
(dev-only); product/architecture ADRs live in `docs/adr/` (published). The publish skill
hard-fails if any known scaffolding path is staged onto master.
