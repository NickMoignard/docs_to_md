# docs-to-okf

A Claude Code **skill** plus a Python **crawler** that mirror an external product's
documentation website into an **OKF v0.1 Knowledge Bundle** — a tree of markdown
**Concepts** with YAML frontmatter — and validate its conformance.

Point it at a documentation site (e.g. `https://docs.stripe.com/payments`) and it
crawls every in-scope page, converts each into a Concept (a clean markdown file with
provenance frontmatter), builds the navigation layer (per-directory listings, a
glossary, a change log, and a bundles index), and validates the result against the
OKF spec so a future agent can navigate the docs without opening hundreds of pages.

Each crawled **Page** becomes one **Concept**. Output lands in the calling repo under
`docs/tools/<tool_name>/`, where each `docs/tools/<tool_name>/` directory is an
independently-validated **OKF Bundle**.

> **Vocabulary note:** throughout this project, **Tool** (capital T) means *the
> external product whose docs you are mirroring* (Stripe, React, …). Each Tool's
> mirror is an OKF **Bundle**; each crawled Page becomes a **Concept** (a markdown
> file with YAML frontmatter). **The skill** and **the crawler** are the things in
> this repo. See [`CONTEXT.md`](CONTEXT.md) for the full domain glossary.

---

## What you get per crawl

For a Tool named `stripe`, in the calling repo:

```
docs/tools/
├── index.md                    # reserved listing of every bundle (frontmatter-free)
└── stripe/
    ├── index.md                # bundle-root listing; carries okf_version: "0.1"
    ├── glossary.md             # type: Glossary — auto-generated domain terms
    ├── log.md                  # dated change history for this bundle
    ├── payments.md             # the /payments Page (a Concept)
    └── payments/               # the /payments section (file-and-folder pattern)
        ├── index.md            # reserved per-directory listing (frontmatter-free)
        └── accept-a-payment.md # one .md Concept per crawled page
```

A URL segment that is **both a page and a parent** uses the **file-and-folder**
pattern — `payments.md` for the page next to a `payments/` directory for its children
(never `payments/index.md`). The reserved `index.md` in each directory is a
frontmatter-free progressive-disclosure listing; the bundle-root `index.md`
additionally carries `okf_version: "0.1"`.

Each Concept file carries YAML frontmatter:

- `type` — the OKF Concept type (required; defaults to `Reference`).
- `title` — the page title.
- `description` — a short summary (written only with `--summarize`).
- `resource` — the source URL the Concept was mirrored from.
- `timestamp` — when it was fetched.
- plus extension keys `keywords`, `content_hash`, and `nav_path`.

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

The skill is the crawler plus the bundled OKF validator: **`SKILL.md`**,
**`crawl.py`**, and **`scripts/validate.sh`**. Clone this repo, then copy them into a
`docs-to-okf` skill directory. You can install it **per-repo** (available only in that
repo) or **globally** (available in every repo).

First, clone this repo somewhere (once):

```bash
git clone https://github.com/NickMoignard/docs_to_okf.git /tmp/docs_to_okf
```

### Option A — per-repo install

Run this from the root of the repo you want the skill in:

```bash
mkdir -p .claude/skills/docs-to-okf/scripts
cp /tmp/docs_to_okf/SKILL.md /tmp/docs_to_okf/crawl.py \
   .claude/skills/docs-to-okf/
cp /tmp/docs_to_okf/scripts/validate.sh \
   .claude/skills/docs-to-okf/scripts/
```

Commit `.claude/skills/docs-to-okf/` so the whole team gets the skill.

### Option B — global install

```bash
mkdir -p ~/.claude/skills/docs-to-okf/scripts
cp /tmp/docs_to_okf/SKILL.md /tmp/docs_to_okf/crawl.py \
   ~/.claude/skills/docs-to-okf/
cp /tmp/docs_to_okf/scripts/validate.sh \
   ~/.claude/skills/docs-to-okf/scripts/
```

The installed layout is `.claude/skills/docs-to-okf/{SKILL.md,crawl.py,scripts/validate.sh}`.
The skill auto-detects which location it lives in (it prefers a per-repo copy and
falls back to the global one), so both can coexist.

### Verify the install

In the target repo, start Claude Code and run:

```
/docs-to-okf https://docs.example.com/
```

If Claude lists `docs-to-okf` among its skills and the command is recognised, you're
set. You can also smoke-test the crawler directly:

```bash
uv run .claude/skills/docs-to-okf/crawl.py https://docs.example.com/ --show-info
```

This prints the derived tool name and crawl scope as JSON without fetching anything.

---

## Using the skill (from Claude Code)

Invoke it with the documentation entry URL:

```
/docs-to-okf <url> [--tool-name <override>] [--fresh]
```

What happens:

1. **Scope preview.** The skill derives a `tool_name` and a **crawl scope**
   (same host + same path prefix as your URL) and shows it to you.
2. **One confirmation.** It asks you to confirm *once*. Reply **yes** (or just press
   Enter) to proceed, **no** to cancel, or type a replacement tool name.
3. **Crawl.** It then crawls the full in-scope site autonomously and writes the OKF
   Bundle to `docs/tools/<tool_name>/`.
4. **Validate & report.** It validates each bundle and prints a summary, e.g.
   `Crawled 142 pages → docs/tools/stripe/`.

Examples:

```
/docs-to-okf https://docs.stripe.com/payments
/docs-to-okf https://react.dev/learn --tool-name react
/docs-to-okf https://docs.stripe.com/payments --fresh
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
| `--type <type>` | OKF Concept type to stamp on each crawled page (default: `Reference`). |
| `--show-info` | Print derived tool name + scope as JSON and exit (no fetching). |
| `--single-page` | Crawl only the given URL, not the whole scope. |
| `--fresh` | Wipe the Tool's cache and output, then re-crawl from scratch. |
| `--convert-only` | Rebuild markdown from the cache without fetching (offline). |
| `--render` | Force Playwright browser rendering for every page. |
| `--safe-mode` | Honour the site's `robots.txt` (ignored by default). |
| `--delay <seconds>` | Inter-request delay (a delay always applies, even by default). |
| `--header 'K: V'` | Extra request header, repeatable (e.g. auth tokens). |
| `--cookie 'name=value'` | Cookie to send, repeatable. |
| `--summarize` | After crawling, write a per-page LLM summary into `description`. |
| `--summarize-concurrency <N>` | Max concurrent summary workers (default 6). |
| `--summarize-batch-size <N>` | Pages per summary batch (default 15). |
| `--synthesize` | Generate the glossary, per-directory listings, and bundles index. |
| `--no-validate` | Skip the post-crawl OKF bundle validation. |

Every crawl ends by validating each bundle with the bundled `scripts/validate.sh`,
which **exits non-zero on conformance errors**. Pass `--no-validate` to skip it.

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
- `crawl.py` — the crawler: discovery, fetch, HTML→Concept conversion, summary
  fan-out, synthesis, and OKF validation.
- `scripts/validate.sh` — the vendored OKF Bundle validator the crawler runs after
  each crawl.
- `CONTEXT.md` — the project's domain glossary (definitions of Tool, Bundle, Concept,
  scope, manifest, glossary, etc.).
- `docs/adr/` — architecture decision records.
- `tests/` — the test suite (`uv run pytest`).

---

## License

[MIT](LICENSE) © 2026 Nick Moignard.
