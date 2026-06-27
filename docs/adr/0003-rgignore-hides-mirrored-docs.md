# Mirrored docs are hidden from ripgrep by default; the navigation layer stays greppable

The whole point of the crawler is to put a Tool's docs in front of an agent — yet a full
mirror is hundreds of Pages of prose that drown out code when an agent greps the repo. So
`docs-to-okf` configures the calling repo to **hide the mirrored docs from ripgrep by
default** while keeping the high-signal **navigation layer** searchable, and tells the
agent how to opt back in.

Concretely, the setup (run after the user confirms a crawl, before fetching) writes a
managed block to the repo-root `.rgignore`:

```
/docs/tools/**
!/docs/tools/**/
!/docs/tools/**/index.md
!/docs/tools/**/glossary.md
!/docs/tools/**/log.md
```

This hides the bulk Pages but keeps the navigation layer greppable: each directory's
progressive-disclosure `index.md` listing, each Tool's `glossary.md` and `log.md`, and the
`docs/tools/index.md` bundles index — so an agent can grep to discover *which* bundle
documents a term, then dive in. To search inside
the bulk docs the agent uses `rg --no-ignore-dot`. A managed block is also added to the
calling repo's `AGENTS.md` (or `CLAUDE.md`) instructing the agent to use `rg` over `grep`
and documenting the opt-in flag.

## Considered Options

- **`.gitignore`** — rejected: the mirrored docs are committed (that *is* the product);
  gitignoring them would untrack them.
- **`.ignore`** — works for ripgrep, but also changes `fd`, `ag`, and other ignore-aware
  tools. Broader blast radius than the stated goal.
- **`.rgignore`** (chosen) — ripgrep-specific, highest precedence; changes exactly the one
  tool we mean and nothing else.
- **Hide all of `docs/tools/`** — simpler one-liner, but an agent can no longer grep to
  find the right entry Page; navigation files become reachable only by path.
  This ADR is re-issued for the renamed OKF nav layer; it is **not** a reversal of the
  hide-bulk-keep-nav decision.
- **Hide bulk Pages, keep the nav layer** (chosen) — fiddlier negation pattern (a parent
  dir must be re-included before its files can be), but preserves cheap grep-to-route.
- **Opt-in via `-u`/`--no-ignore`** — also un-hides `node_modules/`, `.venv/`, etc.,
  re-introducing the noise. Rejected in favor of `--no-ignore-dot`, which disables only the
  dot-ignore files (revealing the docs) while still honoring `.gitignore`.

## Consequences

This inverts the naive expectation: a future reader finds committed docs that do not show
up in `rg` and a non-obvious negation `.rgignore`, hence this record. The convention
propagates into every calling repo and bakes the nav-layer file list into the ignore
pattern, so changing which files stay greppable means re-issuing the `.rgignore` block. It
is per-repo and reversible (delete the block), but the *default* is a deliberate policy.
The opt-in is a single flag: `rg --no-ignore-dot <pattern> docs/tools/`.
