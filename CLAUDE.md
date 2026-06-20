# CLAUDE.md

Guidance for agents working in this repo. **This file is development-only — it is never
published to the public `master` branch.**

## Branch model (read this first)

- You work on **`development`** — the full superset (skill source, crawler, tests, plus
  the AI-dev scaffolding: `.sandcastle/`, `package.json`, `AGENTS.md`, `docs/agents/`,
  in-repo skills, and this file).
- **`master`** is the **public** default branch and a strict *subset* of development.
  Never `git merge development` into master. Publish via the `/publish-to-master` skill,
  which copies only a whitelist. See [`docs/agents/release.md`](docs/agents/release.md)
  and [`docs/agents/adr/0001-two-branch-whitelist-release.md`](docs/agents/adr/0001-two-branch-whitelist-release.md).
- When you add a file that *users* should receive, add it to the whitelist in
  `.claude/skills/publish-to-master/SKILL.md`, or it never ships.

## What ships (public whitelist)

`SKILL.md`, `crawl.py`, `README.md`, `CONTEXT.md`, `docs/adr/`, `tests/`,
`pyproject.toml`, `uv.lock`, `.tool-versions`, `.gitignore`, `LICENSE`.

Everything else is development-only.

## Domain docs

Single-context repo: read `CONTEXT.md` (the glossary) and `docs/adr/` before working on
the crawler or skill. See [`AGENTS.md`](AGENTS.md) for the full agent-doc conventions.

## Running things

```bash
uv run pytest                 # tests
uv run crawl.py <url> --show-info   # smoke-test the crawler (no fetch)
```

`crawl.py` declares its own dependencies via PEP 723 inline metadata, so `uv run` needs
no separate install step.
