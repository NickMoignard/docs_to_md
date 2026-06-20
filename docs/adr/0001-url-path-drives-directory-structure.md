# URL path drives the on-disk directory structure; nav grouping is metadata

We mirror each crawled Tool's documentation so that one Page becomes one markdown
file, placed on disk by its **URL path** (e.g. `/payments/charges` → `payments/charges.md`,
with `<group>/index.md` for group landing pages). The Tool's own sidebar **Nav path**
is preserved only as Page frontmatter (`nav_path`), not used to place files. We chose
this because URL-path placement is deterministic, never duplicates a Page that appears
in two nav sections, places nav-orphan Pages for free, and makes link rewriting a clean
path-to-path mapping.

## Considered Options

- **Nav-sidebar-driven directories** (`Payments/Online payments/Accept a payment.md`) —
  matches the human's mental model and the original "named after the subgroup" wording,
  but is fragile: the nav must be parsed, labels carry spaces/odd characters, a Page can
  live in two nav locations (duplication), and Pages absent from the nav have nowhere to go.
- **URL path for structure + nav captured as metadata** (chosen) — robust filesystem,
  with the curated grouping still recoverable from `nav_path` and surfaced in the per-Tool
  Page map.

## Consequences

Reversing this later reshuffles every output file path **and** every rewritten internal
link, so it is a meaningful migration. The human-curated grouping is not lost — it lives
in `nav_path` and the generated Page map — but it is not reflected in the directory tree.
