---
description: Mirror a Tool's documentation site to markdown. Confirms tool_name and crawl scope with the user once, then crawls the full site into docs/tools/<tool_name>/ without further prompting.
---

# docs-to-md

Crawl a Tool's documentation website and write every in-scope page as markdown into the calling repo's `docs/tools/<tool_name>/` directory.

## Invocation

```
/docs-to-md <url> [--tool-name <override>] [--fresh]
```

- `<url>` — the entry URL of the Tool's documentation site (required)
- `--tool-name` — override the derived tool name (optional)
- `--fresh` — wipe the Tool's cache and output before crawling (optional)

## Steps

### 1. Find the crawler

`crawl.py` is bundled in the same directory as this `SKILL.md`. Locate the skill directory:

```bash
# Per-repo install
SKILL_DIR=".claude/skills/docs-to-md"
# Global install
# SKILL_DIR="$HOME/.claude/skills/docs-to-md"

# Auto-detect: prefer local, fall back to global
if [ -f ".claude/skills/docs-to-md/crawl.py" ]; then
  SKILL_DIR=".claude/skills/docs-to-md"
elif [ -f "$HOME/.claude/skills/docs-to-md/crawl.py" ]; then
  SKILL_DIR="$HOME/.claude/skills/docs-to-md"
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

- **yes / blank** → proceed to step 4 with the derived tool_name.
- **no** → stop; do not crawl.
- **<alternative name>** → use `--tool-name <alternative>` in step 4 and confirm once more.

Do **not** prompt again after this confirmation.

### 4. Crawl

Once confirmed, run the full crawl autonomously:

```bash
uv run "$CRAWLER" <url> [--tool-name <confirmed_tool_name>] [--fresh]
```

The crawler writes markdown to `docs/tools/<tool_name>/` relative to the current working directory (the calling repo's root).

### 5. Report

After crawling completes, print a brief summary:

```
Crawled <N> pages → docs/tools/<tool_name>/
```

List any pages that were skipped (unchanged) if relevant.

## Installation

Copy both `SKILL.md` and `crawl.py` from this repo into the calling repo or your global Claude skills directory.

**Per-repo** (available only in this repo):
```bash
mkdir -p .claude/skills/docs-to-md
cp /path/to/docs_to_md/{SKILL.md,crawl.py} .claude/skills/docs-to-md/
```

**Global** (available in any repo):
```bash
mkdir -p ~/.claude/skills/docs-to-md
cp /path/to/docs_to_md/{SKILL.md,crawl.py} ~/.claude/skills/docs-to-md/
```

After installation, invoke with:
```
/docs-to-md https://docs.example.com/
```
