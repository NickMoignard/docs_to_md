# docs_to_md

The domain of the docs_to_md project: a Claude skill plus a Python crawler that mirror an external product's documentation website into local markdown, then summarise it for agent consumption.

## Language

**Tool**:
An external software product whose documentation website we mirror (e.g. Stripe, React). Its output lives in the calling repo under `docs/tools/<tool_name>/`.
_Avoid_: product, source, library, package (when referring to the documented thing, always say "Tool")

**The crawler**:
The Python script that discovers, fetches, and converts a Tool's documentation pages into local markdown.
_Avoid_: scraper, spider (use "the crawler")

**The skill**:
The Claude skill that orchestrates the crawler and the summarisation fan-out. Installed globally or per-repo and invoked from a calling repo.
_Avoid_: calling the skill a "tool" — "Tool" is reserved for the documented product.

**Page**:
A single canonical documentation URL on the Tool's site. One Page becomes exactly one output markdown file.
_Avoid_: document, doc, article (use "Page" for the unit of crawl/output)

**Crawl scope**:
The boundary that bounds a crawl, auto-derived from the single entry URL as same-host + same-path-prefix. Pages outside the scope are not fetched.
_Avoid_: domain, range

**Nav path**:
The Tool's own curated sidebar grouping for a Page (e.g. "Payments → Online payments → Accept a payment"). Captured as Page metadata (frontmatter `nav_path`), not used to place files on disk.
_Avoid_: breadcrumb, menu path

**Group index**:
The landing Page for a URL-path segment that also contains child Pages (e.g. `/payments`). Stored as `<segment>/index.md` so the segment can be both a directory and a Page.

**Raw page**:
The post-render HTML a Page was converted from (the rendered DOM if Playwright ran, else the HTTP response body). Stored in a gitignored per-Tool cache so conversion is reproducible offline without re-crawling.
_Avoid_: raw HTML, snapshot, source (use "Raw page")

**Manifest**:
The per-Tool record mapping each Page URL to its cached Raw page path, fetch timestamp, ETag/Last-Modified, and content hash. Drives incremental re-crawls.

**Incremental re-crawl**:
A re-run that uses the Manifest's validators (ETag/Last-Modified/content hash) to skip unchanged Pages and re-convert only those that moved.

**Chrome**:
The non-content furniture of a Page's HTML — top nav, sidebar, footer, "on this page" TOC, breadcrumbs, copy buttons, edit links. Stripped before conversion; only the main article is kept.
_Avoid_: boilerplate, furniture (use "Chrome")

**Tabbed code block**:
One logical code example the Tool renders as several hidden tab panels (e.g. npm/yarn/pnpm). Converted to one labeled fenced block per variant (bold label + fence), since markdown has no native tabs.

**Link rewriting**:
The conversion step that turns a Page's hyperlinks into local references: in-scope links present in the Manifest become relative `.md` paths (with `#anchor` preserved); not-crawled, out-of-scope, and external links stay absolute. Standard relative markdown links only — no wikilinks.

**Frontmatter**:
Per-Page YAML header carrying provenance and summary: `title`, `source_url`, `nav_path`, `fetched_at`, `content_hash`, and `summary` (filled by the summarisation fan-out). Images are kept as `![alt](absolute-url)` references; never downloaded.

**Summary**:
A terse routing abstract (~≤50 words) plus the key identifiers (API/function/class/config names) a Page introduces, written for a future agent deciding whether to open the Page. Stored in the Page's `summary` (and `keywords`) frontmatter.
_Avoid_: abstract, description, blurb (use "Summary")

**Candidate term**:
A domain term harvested from a Page during summarisation, proposed for the Tool's glossary. The summarisation fan-out emits these; the synthesis pass consolidates them into `CONTEXT.md`.

**Tools map**:
The top-level `docs/tools/CONTEXT-MAP.md` in the calling repo: one line per Tool (name, what it is, link to its directory). Auto-updated and idempotent on every crawl.
_Avoid_: context map (ambiguous), index

**Page map**:
A per-Tool routing artifact (`docs/tools/<tool>/_index.md`): the URL-path tree of every Page with its one-line summary inline. The primary entry point an agent scans instead of opening hundreds of Pages.
_Avoid_: context map, sitemap, TOC

**Glossary**:
The per-Tool `docs/tools/<tool>/CONTEXT.md`: an auto-generated glossary of the *documented product's* domain language, consolidated from Candidate terms. Marked `<!-- auto-generated -->`. Distinct from a hand-curated project CONTEXT.md.

**Safe mode**:
An opt-in crawl mode (`--safe-mode`) that honors the Tool site's `robots.txt`. Default behavior ignores `robots.txt` (docs are meant to be read, and we typically fetch once), but an inter-request fetch delay always applies so large sites aren't hammered.

## Flagged ambiguities

- **"context map"** was used for two different files. Resolved: the **Tools map** is the top-level `docs/tools/CONTEXT-MAP.md` listing Tools; the per-Tool routing tree of Pages is the **Page map** (`_index.md`). Never call either just "context map".
- **"CONTEXT.md" has two meanings in play.** The repo-root `CONTEXT.md` is this project's hand-curated glossary (the docs_to_md tool). A per-Tool `docs/tools/<tool>/CONTEXT.md` is an auto-generated **Glossary** of the documented product. They never share a directory.

- **Directory structure source**: resolved to the **URL path** (deterministic, no duplication, orphan-safe). The **Nav path** is preserved as metadata only. A Page in two nav sections lives once on disk (by URL) with both nav locations recorded; a Page absent from the nav is still placed by its URL path.

- **"tool"** was originally used for both the thing being built and the documented product. Resolved: **Tool** = the documented product only; the thing being built is **the skill** + **the crawler**.
