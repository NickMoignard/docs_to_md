# Crawler output is an OKF v0.1 Knowledge Bundle, not just markdown

The project's purpose shifts from "mirror a Tool's docs into markdown" to "mirror a
Tool's docs into an **OKF v0.1 Knowledge Bundle**." Each `docs/tools/<tool>/` is one
independently-validated Bundle, and every crawl must pass the OKF validator
([`scripts/validate.sh`](../../scripts/validate.sh)) — the crawler auto-runs it at the
end of a run and exits non-zero on any conformance error. The crawl/fetch/convert
machinery is unchanged; only the **output contract** changes. The crawler is now one
*producer* in the OKF ecosystem (sibling to the general `okf-open-knowledge-format`
skill), so its output interoperates with any OKF consumer.

## What conformance forces (the structural mapping)

OKF v0.1 conformance (validator rules E1–E3) requires every non-reserved `.md` to carry
frontmatter with a non-empty `type`, and reserves `index.md` (a frontmatter-free
directory listing) and `log.md` (dated change history). Three of the crawler's own
artifacts failed this, so the on-disk shape changed:

| Concern | Before | After |
| --- | --- | --- |
| A Page that is also a parent (`/payments`) | `payments/index.md` (concept + frontmatter) — **fails E3** | **`payments.md` + `payments/`** (file-and-folder); `payments/index.md` is a generated listing |
| Routing artifact | `_index.md` (no frontmatter) — **fails E1** | **per-directory `index.md`** progressive-disclosure listings; root carries `okf_version: "0.1"` |
| Per-Tool glossary | `CONTEXT.md` (no frontmatter) — **fails E1** | **`glossary.md`** with `type: Glossary` |
| Cross-bundle catalog | `docs/tools/CONTEXT-MAP.md` (no frontmatter) | **`docs/tools/index.md`** (reserved listing of Bundles) |
| Required `type` | absent | **`type: Reference`** on every crawled Concept (`--type` override later) |
| Frontmatter field names | `source_url`, `fetched_at`, `summary` | `resource`, `timestamp`, `description`; `keywords`/`content_hash`/`nav_path` kept as extension keys |
| Change history | none | generated bundle-root **`log.md`** from the manifest diff |

## Considered Options

- **Stay plain markdown, rebrand only** — rejected: shipping a thing called
  "docs-to-okf" that emits bundles its own validator rejects is dishonest to the spec.
- **Whole-tree root `index.md`** (one file listing everything) vs **per-directory
  progressive disclosure** — chose per-directory: it is the idiomatic OKF pattern the
  validator and consumers expect, at the cost of the old "scan one file" Page map.
- **Absolute bundle-relative links** (`/payments/x.md`, OKF's *preferred* form) vs
  **relative links** — kept relative: a Bundle is a subdirectory of the calling repo, so
  `/…` resolves against the repo root (wrong) in generic markdown tools, and the
  "stable when files move" benefit is moot for a fully regenerated Bundle.
- **`keywords` → `tags`** — rejected: OKF `tags` are categories; our `keywords` are
  API/class identifiers. Kept `keywords` as a preserved extension key.
- **Reimplement the validator in Python** vs **vendor `validate.sh`** — vendored the
  upstream Apache-2.0 script as the authoritative oracle, accepting a bash dependency.

## Consequences

Reversing this reshapes every output path and the frontmatter contract again — a real
migration. Bundles produced without `--summarize` carry no `description` and emit a
non-blocking `W1` warning; that is expected and does not fail validation. The crawler now
has a bash runtime dependency for validation (`--no-validate` escapes it).

Because the renamed listing files (`index.md`, `glossary.md`, `docs/tools/index.md`)
replace the nav-layer filenames baked into the `.rgignore` / `AGENTS.md` rg-policy block,
[ADR-0003](0003-rgignore-hides-mirrored-docs.md) must be re-issued with the new names — the
skill regenerates that block, so the patterns move with the rename.
