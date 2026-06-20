---
description: Publish the clean public subset of this repo from the development branch to master. Copies only a whitelist of files (skill + crawler + public docs) onto master so scaffolding never leaks. Use when releasing docs-to-md changes to the public master branch.
---

# publish-to-master

`master` is the **public** branch: a strict *subset* of `development` containing only
the shipped skill, the crawler, and public-facing docs. `development` is the full
working branch (it also carries `.sandcastle/`, `package.json`, `AGENTS.md`,
`docs/agents/`, `CLAUDE.md`, and this skill).

A plain `git merge development` would drag the scaffolding onto master. So we never
merge — we **copy a fixed whitelist** of paths from development onto master. See
[`docs/agents/adr/0001-two-branch-whitelist-release.md`](../../../docs/agents/adr/0001-two-branch-whitelist-release.md)
for why.

## The whitelist (single source of truth)

Exactly these paths are public. Nothing else ever lands on master.

```
SKILL.md
crawl.py
README.md
CONTEXT.md
CONTRIBUTING.md
LICENSE
pyproject.toml
uv.lock
.tool-versions
.gitignore
docs/adr/
tests/
```

> `docs/adr/` holds **product** ADRs only. Dev-workflow ADRs live in `docs/agents/adr/`,
> which is NOT whitelisted and stays on development.

## Steps

### 1. Preconditions

```bash
# Must be on development with a clean tree.
test "$(git branch --show-current)" = "development" || { echo "switch to development first" >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "commit/stash your work first" >&2; exit 1; }
```

### 2. Rebuild master from the whitelist

```bash
git switch master
# Empty the index entirely (working tree untouched), then stage ONLY the whitelist
# from development. checkout populates both index and working tree, so the commit is
# exactly the whitelist. Do NOT run `git add -A` afterwards — it would re-stage the
# untracked dev-only files still sitting on disk from development and leak them.
git read-tree --empty
git checkout development -- \
  SKILL.md crawl.py README.md CONTEXT.md CONTRIBUTING.md LICENSE \
  pyproject.toml uv.lock .tool-versions .gitignore docs/adr tests
```

> Add a path here (and to "The whitelist" above) whenever a new **public** file is
> introduced, or it will never ship. Keep the two lists in sync.

### 3. Verify no scaffolding leaked

```bash
# Hard fail if any dev-only path is staged for master.
if git ls-files | grep -E '^(\.sandcastle/|package(-lock)?\.json$|AGENTS\.md$|docs/agents/|CLAUDE\.md$|\.claude/)'; then
  echo "ERROR: scaffolding leaked onto master — aborting" >&2
  git switch -f development
  exit 1
fi
```

### 4. Commit and report

```bash
git commit -m "Publish: sync public subset from development" || echo "master already up to date"
# -f because development's dev-only files are still on disk as untracked copies from the
# rebuild; they are byte-identical, so overwriting them is safe.
git switch -f development
```

Tell the user what changed and remind them to `git push origin master` (gated — don't
push without confirmation) and, if the public-facing usage changed, to update `README.md`
on development before the next publish.

### 5. When to update CLAUDE.md

`CLAUDE.md` is **development-only** and never published. If this release changed how
contributors or agents should work in the repo (new commands, new layout, new
conventions), update `CLAUDE.md` on development in the same change — it is the entry
point agents read first.
