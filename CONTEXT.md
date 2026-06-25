# docs-to-okf

The domain of the docs-to-okf project: a Claude skill plus a Python crawler that mirror an external product's documentation website into a local **OKF (Open Knowledge Format) Knowledge Bundle** — one markdown Concept per page, with provenance frontmatter and an optional summary — then validate it for human and agent consumption.

## Language

**OKF (Open Knowledge Format)**:
The external, vendor-neutral v0.1 spec we target: a directory of markdown files with YAML frontmatter where every non-reserved file is a Concept carrying a required `type`. We are a *producer* for OKF (sibling to the general `okf-open-knowledge-format` skill), not its definition.
_Avoid_: "our format", "the md format" (OKF is an external standard we conform to)

**Tool**:
An external software product whose documentation website we mirror (e.g. Stripe, React). Its output lives in the calling repo under `docs/tools/<tool_name>/`.
_Avoid_: product, source, library, package (when referring to the documented thing, always say "Tool")

**The crawler**:
The Python script (`crawl.py`) that discovers, fetches, and converts a Tool's documentation pages into a conformant OKF Bundle, then runs the validator over it.
_Avoid_: scraper, spider (use "the crawler")

**The skill**:
The Claude skill (`docs-to-okf`) that orchestrates the crawler and the summarisation fan-out. Installed globally or per-repo and invoked from a calling repo.
_Avoid_: calling the skill a "tool" — "Tool" is reserved for the documented product.

**Bundle** (Knowledge Bundle):
The unit of OKF distribution: one per-Tool directory `docs/tools/<tool_name>/`, validated independently. A self-contained tree of Concepts.
_Avoid_: package, output dir, mirror (use "Bundle")

**Concept**:
A single unit of knowledge in a Bundle — exactly one markdown file, with frontmatter whose `type` is required. One crawled Page becomes one Concept.
_Avoid_: document, doc, node, record (use "Concept" for the on-disk unit)

**Concept ID**:
A Concept's path within its Bundle with the `.md` suffix removed (e.g. `payments/accept-a-payment.md` → `payments/accept-a-payment`).

**Page**:
A single canonical documentation URL on the Tool's site — the crawl-time *input* unit. One Page is fetched, converted, and written as exactly one Concept.
_Avoid_: article (use "Page" for the URL being crawled, "Concept" for its on-disk output)

**Crawl scope**:
The boundary that bounds a crawl, auto-derived from the single entry URL as same-host + same-path-prefix. Pages outside the scope are not fetched.
_Avoid_: domain, range

**Nav path**:
The Tool's own curated sidebar grouping for a Page (e.g. "Payments → Online payments → Accept a payment"). Captured as Concept metadata (frontmatter `nav_path`), not used to place files on disk.
_Avoid_: breadcrumb, menu path

**Group-index page**:
The landing Page for a URL-path segment that also has child Pages (e.g. `/payments`). Stored as `<segment>.md` *alongside* a `<segment>/` directory of its children (the **file-and-folder** pattern), because OKF reserves `index.md`.
_Avoid_: storing it as `<segment>/index.md` (collides with the reserved listing)

**Reserved file**:
A filename OKF gives defined meaning at any directory level: `index.md` (a frontmatter-free directory listing) and `log.md` (dated change history). Never used for Concepts.

**index.md**:
The reserved per-directory listing for *progressive disclosure*: each directory's immediate Concepts and subdirectories with their one-line descriptions, no frontmatter. The bundle-root `index.md` is the sole exception that may carry frontmatter — only `okf_version: "0.1"`.
_Avoid_: page map, `_index.md`, sitemap, TOC

**log.md**:
The reserved bundle-root change history: `## YYYY-MM-DD` sections (newest first) with Creation/Update/Deprecation entries derived from the Manifest diff on each crawl.

**Bundles index** (`docs/tools/index.md`):
The reserved listing at `docs/tools/`: one entry per Tool/Bundle (name, what it is, link to its directory). Auto-updated and idempotent on every crawl; makes `docs/tools/` itself a valid parent listing.
_Avoid_: tools map, `CONTEXT-MAP.md`, context map

**Raw page**:
The post-render HTML a Page was converted from (the rendered DOM if Playwright ran, else the HTTP response body). Stored in a gitignored per-Tool cache so conversion is reproducible offline without re-crawling.
_Avoid_: raw HTML, snapshot, source (use "Raw page")

**Manifest**:
The per-Tool record mapping each Page URL to its cached Raw page path, fetch timestamp, ETag/Last-Modified, and content hash. Drives incremental re-crawls and the `log.md` diff.

**Incremental re-crawl**:
A re-run that uses the Manifest's validators (ETag/Last-Modified/content hash) to skip unchanged Pages and re-convert only those that moved.

**Chrome**:
The non-content furniture of a Page's HTML — top nav, sidebar, footer, "on this page" TOC, breadcrumbs, copy buttons, edit links. Stripped before conversion; only the main article is kept.
_Avoid_: boilerplate, furniture (use "Chrome")

**Tabbed code block**:
One logical code example the Tool renders as several hidden tab panels (e.g. npm/yarn/pnpm). Converted to one labeled fenced block per variant (bold label + fence), since markdown has no native tabs.

**Link rewriting**:
The conversion step that turns a Page's hyperlinks into local references: in-scope links present in the Manifest become **relative** `.md` paths (with `#anchor` preserved); not-crawled, out-of-scope, and external links stay absolute. Standard relative markdown links only — no wikilinks. (OKF *permits* absolute bundle-relative links but we keep relative so generic markdown tools resolve them correctly.)

**Frontmatter**:
Per-Concept YAML header carrying type, provenance, and summary: `type` (required), `title`, `resource` (the Page's source URL), `timestamp` (when captured), and — with `--summarize` — `description`; plus extension keys `keywords`, `content_hash`, and `nav_path`. Images are kept as `![alt](absolute-url)` references; never downloaded.

**type**:
The required OKF frontmatter field naming a Concept's kind. Crawled Pages default to `Reference`; overridable via `--type`.

**Summary**:
A terse routing abstract (~≤50 words) plus the key identifiers (API/function/class/config names) a Page introduces, written for a future agent deciding whether to open the Concept. Stored in the Concept's `description` (and `keywords`) frontmatter.
_Avoid_: abstract, blurb (use "Summary" for the artifact, `description` for the field)

**Candidate term**:
A domain term harvested from a Page during summarisation, proposed for the Bundle's glossary. The summarisation fan-out emits these; the synthesis pass consolidates them into `glossary.md`.

**Glossary** (`glossary.md`):
The per-Tool `glossary.md`: an auto-generated Concept (`type: Glossary`) of the *documented product's* domain language, consolidated from Candidate terms. Distinct from this project's hand-curated root `CONTEXT.md`.
_Avoid_: a Bundle-internal `CONTEXT.md`, context map

**Conformance / the validator**:
A Bundle is conformant if every non-reserved `.md` has frontmatter with a non-empty `type` and reserved files follow their structure (validator rules E1–E3). `scripts/validate.sh` (vendored, Apache-2.0) is the authoritative oracle; the crawler runs it at the end of every crawl and exits non-zero on any error (`--no-validate` to skip).

**Safe mode**:
An opt-in crawl mode (`--safe-mode`) that honors the Tool site's `robots.txt`. Default behavior ignores `robots.txt` (docs are meant to be read, and we typically fetch once), but an inter-request fetch delay always applies so large sites aren't hammered.

## Flagged ambiguities

- **Page vs Concept.** A **Page** is the crawl-time input (a canonical URL); a **Concept** is its on-disk OKF output (one `.md`). One Page → one Concept. Don't use "page" for the output file.

- **"CONTEXT.md" no longer has two meanings.** Resolved: the per-Tool glossary is now `glossary.md` (`type: Glossary`); the repo-root `CONTEXT.md` is this project's hand-curated glossary and the *only* `CONTEXT.md`. A Bundle never contains a `CONTEXT.md`.

- **Routing/listing files were renamed for OKF.** The old `_index.md` "page map" and `docs/tools/CONTEXT-MAP.md` "tools map" are gone. Per-directory **`index.md`** listings (progressive disclosure) replace the page map; **`docs/tools/index.md`** replaces the tools map. Never call either a "context map".

- **Directory structure source**: the **URL path** drives placement (deterministic, no duplication, orphan-safe); the **Nav path** is metadata only. A segment that is both a page and a parent uses the **file-and-folder** pattern (`<segment>.md` + `<segment>/`), never `<segment>/index.md`.

- **"tool"**: **Tool** = the documented product only; the things we build are **the skill** + **the crawler**. The OKF unit they produce is a **Bundle**.
