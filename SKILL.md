---
description: Mirror a Tool's docs into an OKF v0.1 Knowledge Bundle. Confirms tool_name and crawl scope with the user once, then crawls the full site into docs/tools/<tool_name>/ without further prompting.
---

# docs-to-okf

Crawl a Tool's documentation website and write every in-scope page as markdown into the calling repo's `docs/tools/<tool_name>/` directory, structured as an OKF v0.1 Knowledge Bundle.

## Invocation

```
/docs-to-okf <url> [--tool-name <override>] [--fresh]
```

- `<url>` — the entry URL of the Tool's documentation site (required)
- `--tool-name` — override the derived tool name (optional)
- `--fresh` — wipe the Tool's cache and output before crawling (optional)

## Steps

### 1. Find the crawler

`crawl.py` is bundled in the same directory as this `SKILL.md`. Locate the skill directory:

```bash
# Per-repo install
SKILL_DIR=".claude/skills/docs-to-okf"
# Global install
# SKILL_DIR="$HOME/.claude/skills/docs-to-okf"

# Auto-detect: prefer local, fall back to global
if [ -f ".claude/skills/docs-to-okf/crawl.py" ]; then
  SKILL_DIR=".claude/skills/docs-to-okf"
elif [ -f "$HOME/.claude/skills/docs-to-okf/crawl.py" ]; then
  SKILL_DIR="$HOME/.claude/skills/docs-to-okf"
else
  echo "ERROR: crawl.py not found. Re-install the skill." >&2
  exit 1
fi
CRAWLER="$SKILL_DIR/crawl.py"
```

### 2. Preview tool_name and scope

Run `--show-info` to derive the tool name and crawl scope without fetching any pages:

```bash
uv run "$CRAWLER" <url> --show-info [--tool-name <override>]
```

This outputs a single JSON line, e.g.:
```json
{"tool_name": "stripe", "scope_host": "docs.stripe.com", "scope_prefix": "/payments"}
```

### 3. Confirm once with the user

Present the derived values and ask for confirmation **exactly once**:

> I'll crawl `<scope_host><scope_prefix>` and write markdown to `docs/tools/<tool_name>/`.
>
> - **tool_name**: `<tool_name>`
> - **scope**: `<scope_host><scope_prefix>`
>
> Reply **yes** (or press Enter) to proceed, **no** to cancel, or type a replacement tool_name.

- **yes / blank** → proceed with the derived tool_name (step 4, then crawl).
- **no** → stop; do not crawl.
- **<alternative name>** → use `--tool-name <alternative>` at crawl time (step 5) and confirm once more.

Do **not** prompt again after this confirmation.

### 4. Configure the repo for ripgrep (idempotent)

After the user confirms (step 3) and **before** crawling, set up the calling repo so agents
grep cleanly. All three checks are idempotent — re-running changes nothing once in place. A
cancelled crawl never reaches this step, so the repo is left untouched.

**a. Check ripgrep is installed.** Warn and continue — do not auto-install, do not abort
(the crawl does not need `rg`; the files below are inert until it is installed):

```bash
command -v rg >/dev/null || echo "WARNING: ripgrep (rg) not installed; the .rgignore and AGENTS.md guidance will take effect once you install it (e.g. 'brew install ripgrep' or 'apt install ripgrep'). Continuing." >&2
```

**b. Ensure the `.rgignore` block at the repo root.** Hides the bulk Concept pages from
ripgrep while keeping the navigation layer (each directory's `index.md`, each Tool's
`glossary.md` and `log.md`) greppable. Append only if the marker is absent:

```bash
if ! grep -q "docs-to-okf:rgignore" .rgignore 2>/dev/null; then
  command cat >> .rgignore <<'EOF'
# docs-to-okf:rgignore start
/docs/tools/**
!/docs/tools/**/
!/docs/tools/**/index.md
!/docs/tools/**/glossary.md
!/docs/tools/**/log.md
# docs-to-okf:rgignore end
EOF
fi
```

**c. Ensure the agent-instruction block.** Prefer an existing `AGENTS.md`; else an existing
`CLAUDE.md`; else create `AGENTS.md`. Never write to `CONTEXT.md` (it is a glossary).
Append only if the marker is absent:

```bash
if [ -f AGENTS.md ]; then TARGET=AGENTS.md
elif [ -f CLAUDE.md ]; then TARGET=CLAUDE.md
else TARGET=AGENTS.md
fi
if ! grep -q "docs-to-okf:rg-policy" "$TARGET" 2>/dev/null; then
  command cat >> "$TARGET" <<'EOF'

<!-- docs-to-okf:rg-policy start -->
## Searching the codebase

- Use ripgrep (`rg`) for all searches. **Do not use `grep`** — `rg` is faster and respects ignore files.
- Mirrored Tool docs under `docs/tools/` are hidden from `rg` by default (via `.rgignore`) to keep code searches low-noise. The navigation layer stays searchable: each directory's `index.md`, each Tool's `glossary.md` and `log.md`, and the `docs/tools/index.md` bundles index.
- To search *inside* the mirrored docs, add `--no-ignore-dot`, e.g. `rg --no-ignore-dot "webhook signing" docs/tools/`. (Reveals the docs while still skipping `node_modules/`, `.venv/`, etc.)
<!-- docs-to-okf:rg-policy end -->
EOF
fi
```

### 5. Crawl

Once configured, run the full crawl autonomously:

```bash
uv run "$CRAWLER" <url> [--tool-name <confirmed_tool_name>] [--fresh] --summarize --synthesize
```

`--summarize` fills each Page's `summary` frontmatter via an LLM (one call per Page);
`--synthesize` then writes the OKF navigation layer the crawl is for: a per-directory
progressive-disclosure listing (`index.md`) in every directory, the per-Tool Glossary
(`docs/tools/<tool_name>/glossary.md`), the per-Tool Log (`docs/tools/<tool_name>/log.md`),
and the bundles listing (`docs/tools/index.md`). Without these flags only the raw Page
files are written and there is no navigation layer — which is what the `.rgignore` in
step 4 keeps greppable, so they must run.

The crawler writes markdown to `docs/tools/<tool_name>/` relative to the current working directory (the calling repo's root).

Leave progress **on** (do not pass `--quiet`). The crawler streams live progress to
stderr; when captured (as here) it is throttled to per-phase milestones plus any
failures, so it stays compact while still signalling the crawl is making headway.

### 6. Report

After crawling completes, print a brief summary:

```
Crawled <N> pages → docs/tools/<tool_name>/
  Dir listings: per-directory index.md (progressive disclosure)
  Glossary:     docs/tools/<tool_name>/glossary.md
  Log:          docs/tools/<tool_name>/log.md
  Bundles:      docs/tools/index.md
  OKF: bundle validated — conformant with OKF v0.1
```

List any pages that were skipped (unchanged) or failed to summarize if relevant.

## Installation

The skill is three files that travel together: `SKILL.md`, `crawl.py`, and
`scripts/validate.sh` (the crawler locates the validator at `scripts/validate.sh` next to
itself). Copy all three from this repo into the calling repo or your global Claude skills
directory. The installed layout is `.claude/skills/docs-to-okf/{SKILL.md,crawl.py,scripts/validate.sh}`.

**Per-repo** (available only in this repo):
```bash
mkdir -p .claude/skills/docs-to-okf/scripts
cp /path/to/docs_to_okf/{SKILL.md,crawl.py} .claude/skills/docs-to-okf/
cp /path/to/docs_to_okf/scripts/validate.sh .claude/skills/docs-to-okf/scripts/
```

**Global** (available in any repo):
```bash
mkdir -p ~/.claude/skills/docs-to-okf/scripts
cp /path/to/docs_to_okf/{SKILL.md,crawl.py} ~/.claude/skills/docs-to-okf/
cp /path/to/docs_to_okf/scripts/validate.sh ~/.claude/skills/docs-to-okf/scripts/
```

After installation, invoke with:
```
/docs-to-okf https://docs.example.com/
```
