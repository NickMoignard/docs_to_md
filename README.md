# docs-to-md

A Claude Code **skill** plus a Python **crawler** that mirror an external product's
documentation website into local markdown, then summarise it for agent consumption.

Point it at a documentation site (e.g. `https://docs.stripe.com/payments`) and it
crawls every in-scope page, converts each to a clean markdown file with provenance
frontmatter, and (optionally) writes per-page summaries, a glossary, and a routing
"page map" so a future agent can navigate the docs without opening hundreds of pages.

Output lands in the calling repo under `docs/tools/<tool_name>/`.

> **Vocabulary note:** throughout this project, **Tool** (capital T) means *the
> external product whose docs you are mirroring* (Stripe, React, …). **The skill**
> and **the crawler** are the things in this repo. See [`CONTEXT.md`](CONTEXT.md)
> for the full domain glossary.

---

## What you get per crawl

For a Tool named `stripe`, in the calling repo:

```
docs/tools/
├── CONTEXT-MAP.md              # "Tools map": one line per Tool, auto-updated
└── stripe/
    ├── _index.md               # "Page map": URL tree + one-line summaries
    ├── CONTEXT.md              # "Glossary": auto-generated domain terms
    ├── payments/
    │   ├── index.md            # group-index page for /payments
    │   └── accept-a-payment.md
    └── ...                     # one .md file per crawled page
```

Each page file carries YAML frontmatter (`title`, `source_url`, `nav_path`,
`fetched_at`, `content_hash`, and — with `--summarize` — `summary` / `keywords`).
In-scope links are rewritten to relative `.md` paths; images stay as absolute URLs
(never downloaded).

---

## Requirements

- [`uv`](https://docs.astral.sh/uv/) (Python package manager — runs the crawler and
  manages its dependencies). Version pinned in [`.tool-versions`](.tool-versions).
- Internet access to the Tool's docs site.
- **Only for `--summarize` / `--synthesize`:** an `ANTHROPIC_API_KEY` in the
  environment.

The crawler auto-installs its own headless Chromium (via Playwright) on first run
for pages that need JavaScript rendering — no manual browser setup required.

---

## Installing the skill in another repo

The skill is two files that live next to each other: **`SKILL.md`** and
**`crawl.py`**. Clone this repo, then copy both into a `docs-to-md` skill directory.
You can install it **per-repo** (available only in that repo) or **globally**
(available in every repo).

First, clone this repo somewhere (once):

```bash
git clone https://github.com/NickMoignard/docs_to_md.git /tmp/docs_to_md
```

### Option A — per-repo install

Run this from the root of the repo you want the skill in:

```bash
mkdir -p .claude/skills/docs-to-md
cp /tmp/docs_to_md/SKILL.md /tmp/docs_to_md/crawl.py \
   .claude/skills/docs-to-md/
```

Commit `.claude/skills/docs-to-md/` so the whole team gets the skill.

### Option B — global install

```bash
mkdir -p ~/.claude/skills/docs-to-md
cp /tmp/docs_to_md/SKILL.md /tmp/docs_to_md/crawl.py \
   ~/.claude/skills/docs-to-md/
```

The skill auto-detects which location it lives in (it prefers a per-repo copy and
falls back to the global one), so both can coexist.

### Verify the install

In the target repo, start Claude Code and run:

```
/docs-to-md https://docs.example.com/
```

If Claude lists `docs-to-md` among its skills and the command is recognised, you're
set. You can also smoke-test the crawler directly:

```bash
uv run .claude/skills/docs-to-md/crawl.py https://docs.example.com/ --show-info
```

This prints the derived tool name and crawl scope as JSON without fetching anything.

---

## Using the skill (from Claude Code)

Invoke it with the documentation entry URL:

```
/docs-to-md <url> [--tool-name <override>] [--fresh]
```

What happens:

1. **Scope preview.** The skill derives a `tool_name` and a **crawl scope**
   (same host + same path prefix as your URL) and shows it to you.
2. **One confirmation.** It asks you to confirm *once*. Reply **yes** (or just press
   Enter) to proceed, **no** to cancel, or type a replacement tool name.
3. **Crawl.** It then crawls the full in-scope site autonomously and writes markdown
   to `docs/tools/<tool_name>/`.
4. **Report.** It prints a summary, e.g. `Crawled 142 pages → docs/tools/stripe/`.

Examples:

```
/docs-to-md https://docs.stripe.com/payments
/docs-to-md https://react.dev/learn --tool-name react
/docs-to-md https://docs.stripe.com/payments --fresh
```

The **crawl scope** is what bounds the crawl: only pages on the same host and under
the same URL path prefix as your entry URL are fetched. To mirror a whole docs site,
point at its root (e.g. `https://docs.stripe.com/`); to mirror one section, point at
that section (e.g. `.../payments`).

---

## Using the crawler directly (CLI)

The skill is just orchestration — you can run the crawler yourself. Use the path to
wherever you installed it (per-repo, global, or this repo's root `crawl.py`):

```bash
uv run crawl.py <url> [options]
```

Output is always written relative to the **current working directory**, so `cd` into
the repo you want the docs in before running.

### Common options

| Option | What it does |
| --- | --- |
| `--tool-name <name>` | Override the derived tool name (default: derived from URL host). |
| `--show-info` | Print derived tool name + scope as JSON and exit (no fetching). |
| `--single-page` | Crawl only the given URL, not the whole scope. |
| `--fresh` | Wipe the Tool's cache and output, then re-crawl from scratch. |
| `--convert-only` | Rebuild markdown from the cache without fetching (offline). |
| `--render` | Force Playwright browser rendering for every page. |
| `--safe-mode` | Honour the site's `robots.txt` (ignored by default). |
| `--delay <seconds>` | Inter-request delay (a delay always applies, even by default). |
| `--header 'K: V'` | Extra request header, repeatable (e.g. auth tokens). |
| `--cookie 'name=value'` | Cookie to send, repeatable. |
| `--summarize` | After crawling, write a per-page LLM summary into frontmatter. |
| `--summarize-concurrency <N>` | Max concurrent summary workers (default 6). |
| `--summarize-batch-size <N>` | Pages per summary batch (default 15). |
| `--synthesize` | Generate the glossary, page map, and tools map. |

### Recipes

Preview only, no fetch:

```bash
uv run crawl.py https://docs.stripe.com/payments --show-info
```

Full mirror with summaries and synthesis (needs `ANTHROPIC_API_KEY`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run crawl.py https://docs.stripe.com/payments --summarize --synthesize
```

Re-crawl from scratch:

```bash
uv run crawl.py https://docs.stripe.com/payments --fresh
```

Crawl a docs site that needs authentication:

```bash
uv run crawl.py https://docs.internal.example.com/ \
  --header 'Authorization: Bearer <token>' \
  --cookie 'session=<value>'
```

---

## How re-crawls work

Each Tool has a gitignored cache and a **manifest** recording every page's URL,
cached HTML path, fetch timestamp, ETag/Last-Modified, and content hash. On a normal
re-run the crawler revalidates against those and **skips unchanged pages**, only
re-converting what moved. Use `--fresh` to discard the cache and start over, or
`--convert-only` to rebuild markdown from the existing cache without any network.

By default the crawler **ignores `robots.txt`** (documentation is meant to be read,
and a mirror typically fetches each page once) but always applies an inter-request
delay so sites aren't hammered. Pass `--safe-mode` to honour `robots.txt`.

---

## Project layout

- `SKILL.md` — the Claude Code skill definition (orchestration + the one-time
  confirmation flow).
- `crawl.py` — the crawler: discovery, fetch, HTML→markdown conversion, summary
  fan-out, and synthesis.
- `CONTEXT.md` — the project's domain glossary (definitions of Tool, Page, scope,
  manifest, page map, glossary, etc.).
- `docs/adr/` — architecture decision records.
- `tests/` — the test suite (`uv run pytest`).

---

## License

[MIT](LICENSE) © 2026 Nick Moignard.
</content>
</invoke>
