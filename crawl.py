# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests>=2.31",
#   "beautifulsoup4>=4.12",
#   "markdownify>=0.11",
#   "pyyaml>=6.0",
#   "playwright>=1.60.0",
#   "anthropic>=0.25",
# ]
# ///
"""Crawl documentation pages to markdown — single page or full site."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import markdownify
import requests
import yaml
from bs4 import BeautifulSoup

_COMMON_SUBDOMAINS = {"www", "docs", "api", "developer", "developers", "help", "support"}

_HEADERS = {"User-Agent": "docs-to-okf/1.0 (+https://github.com/NickMoignard/docs_to_okf)"}

_FETCH_DELAY: float = 1.0
_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES: int = 3
_BACKOFF_FACTOR: float = 2.0

_CODE_GUTTER_SELECTORS = [
    ".line-numbers-rows",
    ".gutter",
    ".linenodiv",
    "td.linenos",
]

_CHROME_SELECTORS = [
    "nav",
    "header",
    "footer",
    "[role='navigation']",
    "[role='banner']",
    "[role='contentinfo']",
    ".sidebar",
    ".toc",
    ".on-this-page",
    "[class*='copy-button']",
    "button.copy-button",
    ".copy-button",
    "[aria-label='breadcrumb']",
    ".breadcrumb",
    ".breadcrumbs",
    ".edit-page",
    ".feedback",
    ".page-nav",
    ".DocSearch",
]

_CONTENT_SELECTORS = [
    "main",
    "[role='main']",
    "article",
    ".content",
    ".doc-content",
    ".documentation",
    ".docs-content",
    ".markdown-body",
    "#content",
    "#main-content",
]

_CONTENT_THRESHOLD = 200


class Progress:
    """Live progress reporter for a crawl run.

    Writes to a stream (default ``sys.stderr``). When that stream is a TTY it
    renders a single in-place line per item; otherwise it throttles to milestone
    lines (~every 10% of items) so output captured by the skill/agent stays
    compact. Failures are always printed in full, in both modes.

    Construct one in ``main()`` and pass it into the library functions
    (``crawl_site`` and friends). They stay silent when handed ``None`` — use the
    shared ``_NULL_PROGRESS`` no-op so call sites need no guards. Thread-safe: all
    rendering and counter updates happen under a lock, so the summarize worker
    pool can report concurrently.
    """

    def __init__(self, stream=None, *, enabled: bool = True):
        self._stream = sys.stderr if stream is None else stream
        self.enabled = enabled
        try:
            self.is_tty = bool(self._stream.isatty())
        except Exception:
            self.is_tty = False
        self._lock = threading.Lock()
        self._active = False  # an unfinished in-place TTY line is on screen
        self._label = ""
        self._total = 0
        self._count = 0
        self._tally: dict[str, int] = defaultdict(int)
        self._step = 1
        self._last_emit = 0

    def _clear_active(self) -> None:
        """Erase a pending in-place TTY line. Caller must hold the lock."""
        if self._active and self.is_tty:
            self._stream.write("\r\x1b[K")
            self._stream.flush()
        self._active = False

    def _summary(self) -> str:
        return ", ".join(f"{n} {status}" for status, n in self._tally.items())

    def banner(self, text: str) -> None:
        """Emit a one-off phase banner line (e.g. 'Validating bundle…')."""
        if not self.enabled:
            return
        with self._lock:
            self._clear_active()
            print(text, file=self._stream, flush=True)

    def start(self, label: str, total: int) -> None:
        """Begin a counted phase, resetting counters and the milestone step."""
        if not self.enabled:
            return
        with self._lock:
            self._clear_active()
            self._label = label
            self._total = total
            self._count = 0
            self._tally = defaultdict(int)
            self._step = max(1, total // 10)
            self._last_emit = 0
            print(
                f"{label}: {total} pages" if total else f"{label}…",
                file=self._stream,
                flush=True,
            )

    def begin(self, name: str) -> None:
        """TTY-only: show the in-progress action before an item resolves."""
        if not self.enabled or not self.is_tty:
            return
        with self._lock:
            self._stream.write(f"\r\x1b[K  [{self._count}/{self._total}] {name}…")
            self._stream.flush()
            self._active = True

    def item(self, status: str, name: str, detail: str | None = None) -> None:
        """Report one completed item with its status (fetched/updated/skipped/FAILED)."""
        if not self.enabled:
            return
        with self._lock:
            self._count += 1
            self._tally[status] += 1
            if status == "FAILED":
                self._clear_active()
                line = f"  FAILED {name}"
                if detail:
                    line += f": {detail}"
                print(line, file=self._stream, flush=True)
                return
            if self.is_tty:
                self._stream.write(
                    f"\r\x1b[K  [{self._count}/{self._total}] {status} {name}"
                )
                self._stream.flush()
                self._active = True
            elif self._count - self._last_emit >= self._step or self._count == self._total:
                self._last_emit = self._count
                print(
                    f"  [{self._count}/{self._total}] {self._summary()}",
                    file=self._stream,
                    flush=True,
                )

    def note(self, text: str) -> None:
        """Heartbeat line for unknown-total phases (e.g. BFS discovery)."""
        if not self.enabled:
            return
        with self._lock:
            self._clear_active()
            print(text, file=self._stream, flush=True)

    def done(self, summary: str | None = None) -> None:
        """Finish the current phase, clearing any in-place line and emitting a summary."""
        if not self.enabled:
            return
        with self._lock:
            self._clear_active()
            text = (
                summary
                if summary is not None
                else f"{self._label}: {self._count} done — {self._summary()}"
            )
            print(text, file=self._stream, flush=True)


# Shared no-op so library call sites can report unconditionally when no reporter
# was supplied (every method early-returns on ``enabled is False``).
_NULL_PROGRESS = Progress(enabled=False)


def is_content_below_threshold(markdown: str, threshold: int = _CONTENT_THRESHOLD) -> bool:
    """Return True if stripped markdown length is below threshold."""
    return len(markdown.strip()) < threshold


def ensure_playwright_chromium() -> None:
    """Install Playwright's Chromium browser if not already present (lazy, one-time)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exec_path = Path(p.chromium.executable_path)
    if not exec_path.exists():
        print("Chromium not found — installing via Playwright (one-time setup)…", flush=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


def fetch_with_playwright(url: str) -> str:
    """Fetch a page's fully-rendered HTML via headless Chromium.

    Waits for `networkidle` so client-rendered (SPA) content has settled, but
    falls back to the `domcontentloaded` state when the network never goes idle
    (long-lived analytics/websocket/polling connections keep it busy forever).
    Without the fallback a single such page raises TimeoutError and, since the
    caller has no guard, aborts the entire crawl.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(extra_http_headers=_HEADERS)
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            html = page.content()
        finally:
            browser.close()
    return html


def _fetch_with_retry(
    url: str,
    headers: dict,
    timeout: int = 30,
    max_retries: int = _MAX_RETRIES,
    backoff_factor: float = _BACKOFF_FACTOR,
) -> requests.Response:
    """Fetch url, retrying transient failures with exponential backoff."""
    delay = backoff_factor
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code in _RETRY_STATUSES:
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= backoff_factor
                    continue
                response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout):
            if attempt < max_retries:
                time.sleep(delay)
                delay *= backoff_factor
            else:
                raise


def fetch_robots(entry_url: str) -> RobotFileParser:
    """Fetch and parse robots.txt for the host of entry_url.

    If robots.txt cannot be fetched, the returned parser allows all URLs
    (rather than blocking everything, which would be the default for an
    unread parser per Python's RobotFileParser contract).
    """
    parsed = urlparse(entry_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser(robots_url)
    try:
        rp.read()
    except Exception:
        rp.allow_all = True
    return rp


def is_allowed_by_robots(url: str, robots: RobotFileParser) -> bool:
    """Return True if the crawler's User-Agent is allowed to fetch url."""
    return robots.can_fetch(_HEADERS["User-Agent"], url)


def derive_tool_name(url: str) -> str:
    """Derive tool_name from the URL host, stripping common subdomains."""
    host = urlparse(url).netloc
    parts = host.split(".")
    while len(parts) > 2 and parts[0] in _COMMON_SUBDOMAINS:
        parts = parts[1:]
    return parts[0]


def derive_page_path(url: str) -> str:
    """Convert URL path to a relative file path (no leading slash, no .md extension).

    Under the OKF file-and-folder layout, a page maps directly to <path>; a
    trailing slash is treated as the same page (no "/index" promotion). The
    reserved name "index" is only ever used for generated directory listings,
    never for a crawled page — see compute_output_path / generate_indexes.
    """
    parsed_path = urlparse(url).path
    path = parsed_path.strip("/")
    if not path:
        return "_root"
    return path


def derive_crawl_scope(entry_url: str) -> tuple[str, str]:
    """
    Derive crawl scope from the entry URL.

    Returns (netloc, path_prefix) where path_prefix is the URL path
    (or "/" for root). Pages outside this host+prefix are not fetched.
    """
    parsed = urlparse(entry_url)
    path = parsed.path.rstrip("/") or "/"
    return (parsed.netloc, path)


def is_in_scope(url: str, scope: tuple[str, str]) -> bool:
    """Return True if url belongs to the same host and path prefix as scope."""
    netloc, path_prefix = scope
    parsed = urlparse(url)
    if parsed.netloc != netloc:
        return False
    url_path = parsed.path if parsed.path else "/"
    if path_prefix == "/":
        return True
    return url_path == path_prefix or url_path.startswith(path_prefix + "/")


def extract_page_links(html: str, base_url: str, scope: tuple[str, str]) -> list[str]:
    """Extract all in-scope, deduplicated page links from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    links: list[str] = []

    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        resolved = urljoin(base_url, href)
        parsed = urlparse(resolved)
        if parsed.scheme not in ("http", "https"):
            continue
        normalized = urlunparse(parsed._replace(fragment="", query=""))
        if normalized in seen:
            continue
        if is_in_scope(normalized, scope):
            seen.add(normalized)
            links.append(normalized)

    return links


def fetch_sitemap_urls(entry_url: str, scope: tuple[str, str]) -> list[str] | None:
    """
    Try to fetch /sitemap.xml and return in-scope URLs.

    Returns None if the sitemap is unavailable or unparseable.
    """
    parsed = urlparse(entry_url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

    try:
        response = requests.get(sitemap_url, headers=_HEADERS, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        # A <sitemapindex> points to sub-sitemaps, not pages — follow each one
        # and collect the in-scope page URLs they contain.
        if root.findall("sm:sitemap", ns):
            all_urls: list[str] = []
            for loc_el in root.findall(".//sm:loc", ns):
                if not loc_el.text:
                    continue
                try:
                    sub = requests.get(loc_el.text.strip(), headers=_HEADERS, timeout=30)
                    sub.raise_for_status()
                    sub_root = ET.fromstring(sub.text)
                    all_urls.extend(
                        loc.text.strip()
                        for loc in sub_root.findall(".//sm:loc", ns)
                        if loc.text and is_in_scope(loc.text.strip(), scope)
                    )
                except (requests.RequestException, ET.ParseError):
                    continue
            return all_urls if all_urls else None

        # A <urlset> lists page URLs directly.
        urls = [
            loc.text.strip()
            for loc in root.findall(".//sm:loc", ns)
            if loc.text and is_in_scope(loc.text.strip(), scope)
        ]
        return urls if urls else None
    except (requests.RequestException, ET.ParseError):
        return None


def compute_output_path(url: str, all_urls: list[str]) -> str:
    """
    Compute the on-disk page path under the OKF file-and-folder layout.

    A page is always written as <path>.md, whether or not it has children.
    A parent page (one that has child pages in all_urls) is written as
    <path>.md and its children live under <path>/ — the filename "index.md"
    is reserved for generated directory listings and is NEVER used for a page.
    """
    return derive_page_path(url)


def raw_cache_dir(tool_name: str, base_dir: Path | None = None) -> Path:
    """Return the raw-page cache directory for a Tool."""
    if base_dir is None:
        base_dir = Path(".")
    return base_dir / ".cache" / "tools" / tool_name / "raw"


def manifest_path(tool_name: str, base_dir: Path | None = None) -> Path:
    """Return the manifest file path for a Tool."""
    if base_dir is None:
        base_dir = Path(".")
    return base_dir / ".cache" / "tools" / tool_name / "manifest.json"


def load_manifest(tool_name: str, base_dir: Path | None = None) -> dict:
    """Load the Tool manifest, returning an empty dict if none exists."""
    path = manifest_path(tool_name, base_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict, tool_name: str, base_dir: Path | None = None) -> None:
    """Persist the Tool manifest to disk."""
    path = manifest_path(tool_name, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def cache_raw_page(html: str, page_path: str, tool_name: str, base_dir: Path | None = None) -> Path:
    """Save raw HTML to the per-Tool cache, returning the path written."""
    cache_path = raw_cache_dir(tool_name, base_dir) / (page_path + ".html")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(html, encoding="utf-8")
    return cache_path


def wipe_tool_data(tool_name: str, base_dir: Path | None = None) -> None:
    """Delete the cache and output directories for a Tool."""
    if base_dir is None:
        base_dir = Path(".")
    for target in (
        base_dir / ".cache" / "tools" / tool_name,
        base_dir / "docs" / "tools" / tool_name,
    ):
        if target.exists():
            shutil.rmtree(target)


def convert_from_cache(
    url: str,
    entry: dict,
    tool_name: str,
    base_dir: Path | None = None,
    manifest: dict | None = None,
    type_: str = "Reference",
) -> Path | None:
    """Re-convert a cached Raw page to markdown without any network fetch."""
    if base_dir is None:
        base_dir = Path(".")
    cache_path = base_dir / entry["cache_path"]
    if not cache_path.exists():
        return None

    html = cache_path.read_text(encoding="utf-8")
    page_path = entry["page_path"]
    output_path = base_dir / "docs" / "tools" / tool_name / (page_path + ".md")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    nav_path = entry.get("nav_path")
    title, content_html = extract_content(html)

    content_soup = BeautifulSoup(content_html, "html.parser")
    if manifest is not None:
        rewrite_links(content_soup, url, manifest, page_path)
    make_images_absolute(content_soup, url)
    markdown_body = to_markdown(str(content_soup))

    # Preserve any existing summary/keywords already written to the page so a
    # re-conversion does not drop the LLM-generated description.
    existing_fm = read_page_frontmatter(output_path) if output_path.exists() else {}
    frontmatter_text = build_frontmatter(
        title,
        url,
        entry.get("fetched_at", ""),
        entry.get("content_hash", ""),
        nav_path=nav_path,
        type_=type_,
        description=existing_fm.get("description"),
        keywords=existing_fm.get("keywords"),
    )
    output_path.write_text(frontmatter_text + markdown_body, encoding="utf-8")
    return output_path


def _bfs_discover(
    entry_url: str,
    scope: tuple[str, str],
    safe_mode: bool = False,
    robots: RobotFileParser | None = None,
    extra_headers: dict | None = None,
    fetch_delay: float = 0.0,
    progress: "Progress | None" = None,
) -> list[str]:
    """BFS discovery of all in-scope pages starting from entry_url.

    progress, if provided, gets a throttled heartbeat as the (unknown) total grows.
    """
    prog = progress if progress is not None else _NULL_PROGRESS
    visited: set[str] = set()
    queue: deque[str] = deque([entry_url])
    ordered: list[str] = []

    headers = dict(_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    first = True
    while queue:
        url = queue.popleft()
        if url in visited:
            continue
        if safe_mode and robots and not is_allowed_by_robots(url, robots):
            continue
        visited.add(url)
        ordered.append(url)
        if len(ordered) % 25 == 0:
            prog.note(f"Discovering: {len(ordered)} URLs so far…")

        if not first and fetch_delay > 0:
            time.sleep(fetch_delay)
        first = False

        try:
            response = _fetch_with_retry(url, headers, timeout=30)
            response.raise_for_status()
            for link in extract_page_links(response.text, url, scope):
                if link not in visited:
                    queue.append(link)
        except requests.RequestException:
            continue

    prog.note(f"Discovered {len(ordered)} URLs via link-following.")
    return ordered


def _detect_language(element) -> str:
    """Detect code language from class or data attributes on a BeautifulSoup element."""
    for attr in ("data-lang", "data-language"):
        if val := element.get(attr):
            return val.strip()
    for cls in element.get("class") or []:
        for prefix in ("language-", "lang-", "highlight-"):
            if cls.startswith(prefix):
                lang = cls[len(prefix):]
                if lang not in ("none", "plaintext", "text"):
                    return lang
    return ""


def _get_language_for_pre(pre) -> str:
    """Return language for a <pre>, checking its <code> child first."""
    code = pre.find("code")
    if code:
        lang = _detect_language(code)
        if lang:
            return lang
    return _detect_language(pre)


def _get_clean_code_text(pre) -> str:
    """Strip gutters and buttons from <pre> in-place, then return text content."""
    for sel in _CODE_GUTTER_SELECTORS:
        for el in pre.select(sel):
            el.decompose()
    for btn in pre.find_all("button"):
        btn.decompose()
    code = pre.find("code")
    return code.get_text() if code else pre.get_text()


def _build_clean_pre(lang: str, text: str, soup):
    new_pre = soup.new_tag("pre")
    new_code = soup.new_tag("code")
    if lang:
        new_code["class"] = [f"language-{lang}"]
    new_code.string = text
    new_pre.append(new_code)
    return new_pre


def _expand_tab_container(container, labels, items, soup) -> None:
    inserts = []
    for i, item in enumerate(items):
        pre = item.find("pre")
        if not pre:
            continue
        label = labels[i] if i < len(labels) else f"Tab {i + 1}"
        lang = _get_language_for_pre(pre)
        text = _get_clean_code_text(pre)
        p = soup.new_tag("p")
        strong = soup.new_tag("strong")
        strong.string = label
        p.append(strong)
        inserts.append(p)
        inserts.append(_build_clean_pre(lang, text, soup))
    for el in inserts:
        container.insert_before(el)
    container.decompose()


def _handle_tabbed_set(tabset, soup) -> None:
    """Replace a .tabbed-set with sequential labeled <pre> blocks."""
    labels = [el.get_text(strip=True) for el in tabset.select(".tabbed-labels label")]
    _expand_tab_container(tabset, labels, tabset.select(".tabbed-block"), soup)


def _handle_aria_tabs(container, soup) -> None:
    """Replace a generic ARIA tablist/tabpanel container with sequential labeled <pre> blocks."""
    tablist = container.find(attrs={"role": "tablist"})
    if not tablist:
        return
    labels = [el.get_text(strip=True) for el in tablist.find_all(attrs={"role": "tab"})]
    _expand_tab_container(container, labels, container.find_all(attrs={"role": "tabpanel"}), soup)


def preprocess_code_blocks(soup) -> None:
    """
    Mutate soup in place to produce clean, faithful code blocks:

    - Expand .tabbed-set (Material for MkDocs) into sequential labeled <pre> blocks.
    - Expand generic ARIA tablist/tabpanel containers the same way.
    - For every remaining <pre>: strip gutter elements and copy buttons, read
      text content (never innerHTML) to remove syntax-highlight span soup, and
      re-emit as a clean <pre><code class="language-LANG"> element.
    """
    for tabset in list(soup.find_all("div", class_="tabbed-set")):
        _handle_tabbed_set(tabset, soup)

    for tablist_el in list(soup.find_all(attrs={"role": "tablist"})):
        parent = tablist_el.parent
        if parent and parent.name not in ("html", "body", "[document]"):
            _handle_aria_tabs(parent, soup)

    for pre in list(soup.find_all("pre")):
        lang = _get_language_for_pre(pre)
        text = _get_clean_code_text(pre)
        pre.replace_with(_build_clean_pre(lang, text, soup))


def extract_content(html: str) -> tuple[str, str]:
    """
    Strip Chrome and extract main content from HTML.

    Returns (title, content_html) where title is the page title and
    content_html is the stripped main content as an HTML string.
    """
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if tag := soup.find("title"):
        title = tag.get_text(strip=True)
    if tag := soup.find("h1"):
        title = tag.get_text(strip=True)

    for selector in _CHROME_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    content = None
    for selector in _CONTENT_SELECTORS:
        content = soup.select_one(selector)
        if content:
            break

    if content is None:
        content = soup.body or soup

    preprocess_code_blocks(soup)

    return title, str(content)


def _code_language_callback(pre_el) -> str:
    code = pre_el.find("code")
    if code:
        for cls in code.get("class") or []:
            if cls.startswith("language-"):
                return cls[len("language-"):]
    return ""


def to_markdown(html: str) -> str:
    """Convert an HTML string to markdown."""
    return markdownify.markdownify(
        html,
        heading_style="ATX",
        bullets="-",
        code_language_callback=_code_language_callback,
    )


def extract_nav_path(html: str, current_url: str) -> list[str]:
    """Extract sidebar navigation group labels for the current page from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")
    current_path = urlparse(current_url).path.rstrip("/") or "/"

    active_link = None
    container = None

    nav_elements = soup.find_all(["nav", "aside"])

    # Priority 1: aria-current attribute
    for nav in nav_elements:
        link = nav.find("a", attrs={"aria-current": True})
        if link:
            active_link = link
            container = nav
            break

    # Priority 2: common active CSS classes
    if not active_link:
        for nav in nav_elements:
            for cls in ("active", "current", "is-active", "is-current", "selected"):
                link = nav.find("a", class_=cls)
                if link:
                    active_link = link
                    container = nav
                    break
            if active_link:
                break

    # Priority 3: URL path matching
    if not active_link:
        for nav in nav_elements:
            for a in nav.find_all("a", href=True):
                resolved = urljoin(current_url, a["href"])
                link_path = urlparse(resolved).path.rstrip("/") or "/"
                if link_path == current_path:
                    active_link = a
                    container = nav
                    break
            if active_link:
                break

    if not active_link or container is None:
        return []

    # Walk up the DOM from the active link, collecting preceding section labels
    _label_elements = ("h1", "h2", "h3", "h4", "h5", "h6", "summary", "strong", "button")
    labels: list[str] = []
    node = active_link.parent
    while node and node is not container:
        for prev in node.find_previous_siblings():
            if prev.name in _label_elements:
                text = prev.get_text(strip=True)
                if text:
                    labels.insert(0, text)
                    break
        node = node.parent

    return labels


def rewrite_links(soup: BeautifulSoup, base_url: str, manifest: dict, current_page_path: str) -> None:
    """Rewrite <a href> links in soup in-place.

    Links whose resolved URL appears in the manifest become relative .md paths
    (with fragment preserved). All other links are made absolute.
    """
    url_to_page_path = {url: entry["page_path"] for url, entry in manifest.items()}
    current_dir = str(Path(current_page_path).parent)

    for tag in soup.find_all("a", href=True):
        href = tag["href"]

        if href.startswith("#"):
            continue

        resolved = urljoin(base_url, href)
        parsed = urlparse(resolved)

        if parsed.scheme not in ("http", "https"):
            continue

        fragment = parsed.fragment
        normalized = urlunparse(parsed._replace(fragment="", query=""))

        if normalized in url_to_page_path:
            linked_path = url_to_page_path[normalized] + ".md"
            rel = os.path.relpath(linked_path, current_dir).replace("\\", "/")
            tag["href"] = (rel + "#" + fragment) if fragment else rel
        else:
            tag["href"] = resolved


def make_images_absolute(soup: BeautifulSoup, base_url: str) -> None:
    """Resolve all <img src> attributes to absolute URLs in place."""
    for tag in soup.find_all("img", src=True):
        src = tag["src"]
        if not src.startswith("data:"):
            tag["src"] = urljoin(base_url, src)


def build_frontmatter_block(data: dict) -> str:
    """Serialize an arbitrary mapping as a YAML frontmatter block (keys in order)."""
    return (
        "---\n"
        + yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        + "---\n\n"
    )


def build_frontmatter(
    title: str,
    source_url: str,
    fetched_at: str,
    content_hash: str,
    nav_path: list[str] | None = None,
    type_: str = "Reference",
    description: str | None = None,
    keywords: list[str] | None = None,
) -> str:
    """Build an OKF-conformant YAML frontmatter block.

    Emits the required `type` field first, then maps crawler metadata onto OKF
    field names: `resource` (the source URL) and `timestamp` (the fetch time).
    `content_hash` and `nav_path` are producer-defined extension keys.
    When summarized, the LLM abstract is written to `description` (with
    `keywords`).
    """
    # Build with an explicit ordering so `type` is emitted first.
    data: dict = {"type": type_, "title": title}
    if description:
        data["description"] = description
    data["resource"] = source_url
    data["timestamp"] = fetched_at
    data["content_hash"] = content_hash
    if keywords:
        data["keywords"] = keywords
    if nav_path:
        data["nav_path"] = nav_path
    return (
        "---\n"
        + yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        + "---\n\n"
    )


def crawl_page(
    url: str,
    tool_name: str | None = None,
    base_dir: Path | None = None,
    page_path: str | None = None,
    manifest: dict | None = None,
    force_render: bool = False,
    extra_headers: dict | None = None,
    type_: str = "Reference",
) -> Path | None:
    """
    Fetch a single documentation page and write it as markdown.

    Args:
        url: The page URL to crawl.
        tool_name: Override tool name; defaults to host-derived name.
        base_dir: Root directory for output (defaults to cwd).
        page_path: Override the output path (no .md extension); defaults to derive_page_path(url).
        manifest: Live manifest dict; if provided, validators are sent and the entry is updated in place.
        force_render: Skip static fetch and use Playwright for all pages.
        extra_headers: Additional request headers (e.g. auth tokens, cookies).

    Returns:
        Path to the written markdown file, or None if the page was skipped or errored.
    """
    if tool_name is None:
        tool_name = derive_tool_name(url)
    if base_dir is None:
        base_dir = Path(".")
    if page_path is None:
        page_path = derive_page_path(url)

    entry = (manifest or {}).get(url, {})

    if force_render:
        ensure_playwright_chromium()
        try:
            html = fetch_with_playwright(url)
        except Exception as exc:
            if manifest is not None:
                manifest[url] = {"page_path": page_path, "status": "errored", "error": str(exc)}
            return None
        etag = None
        last_modified = None
    else:
        req_headers = dict(_HEADERS)
        if extra_headers:
            req_headers.update(extra_headers)
        if entry.get("etag"):
            req_headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            req_headers["If-Modified-Since"] = entry["last_modified"]

        try:
            response = _fetch_with_retry(url, req_headers, timeout=30)
        except requests.RequestException as exc:
            if manifest is not None:
                manifest[url] = {"page_path": page_path, "status": "errored", "error": str(exc)}
            return None

        if response.status_code == 304:
            return None

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            if manifest is not None:
                manifest[url] = {"page_path": page_path, "status": "errored", "error": str(exc)}
            return None

        html = response.text
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")

    content_hash = hashlib.sha256(html.encode()).hexdigest()

    if entry.get("content_hash") == content_hash:
        return None

    fetched_at = datetime.now(UTC).isoformat()

    title, content_html = extract_content(html)
    markdown_body = to_markdown(content_html)

    if not force_render and is_content_below_threshold(markdown_body):
        ensure_playwright_chromium()
        try:
            html = fetch_with_playwright(url)
        except Exception as exc:
            if manifest is not None:
                manifest[url] = {"page_path": page_path, "status": "errored", "error": str(exc)}
            return None
        content_hash = hashlib.sha256(html.encode()).hexdigest()
        title, content_html = extract_content(html)
        markdown_body = to_markdown(content_html)
        etag = None
        last_modified = None

    cache_path = cache_raw_page(html, page_path, tool_name, base_dir)

    nav_path = extract_nav_path(html, url)

    if manifest is not None:
        manifest[url] = {
            "page_path": page_path,
            "cache_path": str(cache_path.relative_to(base_dir)),
            "fetched_at": fetched_at,
            "etag": etag,
            "last_modified": last_modified,
            "content_hash": content_hash,
            "nav_path": nav_path,
        }

    output_path = base_dir / "docs" / "tools" / tool_name / (page_path + ".md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content_soup = BeautifulSoup(content_html, "html.parser")
    if manifest is not None:
        rewrite_links(content_soup, url, manifest, page_path)
    make_images_absolute(content_soup, url)
    markdown_body = to_markdown(str(content_soup))

    # Preserve any existing summary/keywords from a prior summarize pass.
    existing_fm = read_page_frontmatter(output_path) if output_path.exists() else {}
    frontmatter = build_frontmatter(
        title,
        url,
        fetched_at,
        content_hash,
        nav_path=nav_path,
        type_=type_,
        description=existing_fm.get("description"),
        keywords=existing_fm.get("keywords"),
    )
    output_path.write_text(frontmatter + markdown_body, encoding="utf-8")
    return output_path


def crawl_site(
    entry_url: str,
    tool_name: str | None = None,
    base_dir: Path | None = None,
    convert_only: bool = False,
    fresh: bool = False,
    force_render: bool = False,
    safe_mode: bool = False,
    extra_headers: dict | None = None,
    fetch_delay: float = _FETCH_DELAY,
    errors: list | None = None,
    type_: str = "Reference",
    diff: dict | None = None,
    progress: "Progress | None" = None,
) -> list[Path]:
    """
    Crawl all in-scope pages from entry_url and write each as markdown.

    Discovery uses sitemap.xml when present, BFS link-following otherwise.
    Crawl scope is auto-derived as same host + path prefix of entry_url.
    Under the OKF file-and-folder layout, a parent page is written as
    <path>.md and its children live under <path>/; "index.md" is reserved
    for generated directory listings.

    Args:
        convert_only: Rebuild markdown from the Raw-page cache without any network fetches.
        fresh: Wipe the Tool's cache and output before crawling.
        safe_mode: Honor the site's robots.txt (ignored by default per ADR-0002).
        extra_headers: Additional request headers passed to every fetch (auth, cookies, etc.).
        fetch_delay: Seconds to sleep between page fetches (default: 1.0).
        errors: If provided, dicts with "url" and "error" are appended for each failed page.
        diff: If provided, populated with {"created": [page_path...], "updated": [page_path...]}
            distinguishing newly-discovered URLs from URLs whose content_hash changed.
        progress: If provided, receives live per-page progress; None stays silent.

    Returns:
        List of paths to written (or re-converted) markdown files.
    """
    if tool_name is None:
        tool_name = derive_tool_name(entry_url)
    if base_dir is None:
        base_dir = Path(".")

    if fresh:
        wipe_tool_data(tool_name, base_dir)

    manifest = load_manifest(tool_name, base_dir)
    # Snapshot pre-crawl state so we can classify created vs updated Concepts.
    prior_hashes = {
        url: entry.get("content_hash") for url, entry in manifest.items()
    }

    if convert_only:
        written = []
        for url, entry in manifest.items():
            result = convert_from_cache(
                url, entry, tool_name, base_dir, manifest=manifest, type_=type_
            )
            if result is not None:
                written.append(result)
        return written

    prog = progress if progress is not None else _NULL_PROGRESS
    robots = fetch_robots(entry_url) if safe_mode else None
    scope = derive_crawl_scope(entry_url)
    sitemap_urls = fetch_sitemap_urls(entry_url, scope)
    if sitemap_urls:
        prog.note(f"Discovered {len(sitemap_urls)} URLs via sitemap.")
        raw_urls = sitemap_urls
    else:
        raw_urls = _bfs_discover(
            entry_url, scope,
            safe_mode=safe_mode,
            robots=robots,
            extra_headers=extra_headers,
            fetch_delay=fetch_delay,
            progress=prog,
        )

    if safe_mode and robots:
        urls = [u for u in raw_urls if is_allowed_by_robots(u, robots)]
    else:
        urls = raw_urls

    prog.start("Crawling", len(urls))

    written = []
    created: list[str] = []
    updated: list[str] = []
    for i, url in enumerate(urls):
        if i > 0 and fetch_delay > 0:
            time.sleep(fetch_delay)
        path = compute_output_path(url, urls)
        was_known = url in prior_hashes
        prog.begin(path)
        result = crawl_page(
            url,
            tool_name=tool_name,
            base_dir=base_dir,
            page_path=path,
            manifest=manifest,
            force_render=force_render,
            extra_headers=extra_headers,
            type_=type_,
        )
        if result is not None:
            written.append(result)
            page_path = manifest.get(url, {}).get("page_path", path)
            if was_known:
                updated.append(page_path)
                prog.item("updated", page_path)
            else:
                created.append(page_path)
                prog.item("fetched", page_path)
        else:
            entry = manifest.get(url, {})
            if entry.get("status") == "errored":
                error = entry.get("error", "unknown")
                prog.item("FAILED", path, error)
                if errors is not None:
                    errors.append({"url": url, "error": error})
            else:
                prog.item("skipped", path)

    prog.done()

    if diff is not None:
        diff["created"] = created
        diff["updated"] = updated

    save_manifest(manifest, tool_name, base_dir)
    return written


def batch_pages_by_directory(urls: list[str], batch_size: int = 15) -> list[list[str]]:
    """Group URLs by URL-path parent directory, splitting large groups into batches."""
    groups: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        path = urlparse(url).path
        dir_key = str(PurePosixPath(path).parent)
        groups[dir_key].append(url)

    batches: list[list[str]] = []
    for group_urls in groups.values():
        for i in range(0, len(group_urls), batch_size):
            batches.append(group_urls[i : i + batch_size])
    return batches


def read_page_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a markdown file, returning {} when absent."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    try:
        end = text.index("---\n", 4)
    except ValueError:
        return {}
    return yaml.safe_load(text[4:end]) or {}


def write_page_frontmatter(path: Path, updates: dict) -> None:
    """Merge updates into a markdown file's frontmatter, preserving the body."""
    text = path.read_text(encoding="utf-8")
    data: dict = {}
    body = text
    if text.startswith("---\n"):
        try:
            end = text.index("---\n", 4)
            data = yaml.safe_load(text[4:end]) or {}
            body = text[end + 4:]
        except ValueError:
            pass

    data.update(updates)
    new_fm = "---\n" + yaml.dump(data, default_flow_style=False, allow_unicode=True) + "---\n\n"
    path.write_text(new_fm + body.lstrip("\n"), encoding="utf-8")


def is_page_summarized(path: Path) -> bool:
    """Return True if the page's frontmatter contains a non-empty description."""
    return bool(read_page_frontmatter(path).get("description"))


def _strip_code_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text


def _parse_json_array_tolerant(text: str) -> list:
    """Parse a JSON array of objects, salvaging entries from malformed output.

    LLM glossary output can arrive truncated (hit max_tokens) or with a trailing
    comma. A strict json.loads throws on the whole payload and loses every entry.
    Try strict first; on failure, extract each complete top-level {...} object and
    parse it individually, discarding any incomplete trailing object.
    """
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        pass

    entries: list = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    entries.append(json.loads(text[start : i + 1]))
                except json.JSONDecodeError:
                    pass
                start = -1
    return entries


def call_summarize_llm(content: str, url: str) -> dict:
    """Call Claude to generate a summary, keywords, and candidate terms for a page.

    Returns {"summary": str, "keywords": list[str], "candidates": list[str]}.
    Requires ANTHROPIC_API_KEY in the environment.
    """
    import anthropic

    client = anthropic.Anthropic()
    prompt = (
        "You are a documentation indexer. Given a markdown page, return a JSON object with:\n"
        '- "summary": terse routing abstract, ≤50 words, for an agent deciding whether to open the page\n'
        '- "keywords": list of API/function/class/config names introduced on this page\n'
        '- "candidates": list of domain terms (concepts) worth adding to a glossary\n\n'
        "Return only valid JSON, nothing else.\n\n"
        f"URL: {url}\n\n"
        f"Content:\n{content[:6000]}"
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _strip_code_fences(response.content[0].text.strip())
    parsed = json.loads(text.strip())
    return {
        "summary": str(parsed.get("summary", "")),
        "keywords": list(parsed.get("keywords", [])),
        "candidates": list(parsed.get("candidates", [])),
    }


def _summarize_batch(
    urls: list[str],
    manifest: dict,
    tool_name: str,
    base_dir: Path,
    progress: "Progress | None" = None,
) -> dict:
    """Summarize a batch of pages by calling the LLM and writing results to frontmatter.

    Skips pages whose frontmatter content_hash matches the manifest (unchanged) and
    that already have a non-empty description. Returns candidates, done count, failed count.

    progress, if provided, receives one item() call per page; it is shared across
    the summarize worker pool, so updates are made under its lock.
    """
    prog = progress if progress is not None else _NULL_PROGRESS
    candidates: list[str] = []
    done = 0
    failed = 0

    for url in urls:
        entry = manifest.get(url)
        if not entry or not entry.get("page_path"):
            failed += 1
            prog.item("FAILED", url, "no page_path in manifest")
            continue

        page_path = entry["page_path"]
        md_path = base_dir / "docs" / "tools" / tool_name / (page_path + ".md")
        if not md_path.exists():
            failed += 1
            prog.item("FAILED", page_path, "markdown file missing")
            continue

        fm = read_page_frontmatter(md_path)
        if fm.get("description") and fm.get("content_hash") == entry.get("content_hash"):
            prog.item("skipped", page_path)
            continue

        try:
            content = md_path.read_text(encoding="utf-8")
            result = call_summarize_llm(content, url)
            # The LLM JSON uses "summary"/"keywords"; map summary → the OKF
            # `description` frontmatter key.
            write_page_frontmatter(md_path, {
                "description": result["summary"],
                "keywords": result["keywords"],
            })
            candidates.extend(result["candidates"])
            done += 1
            prog.item("summarized", page_path)
        except Exception as exc:
            failed += 1
            prog.item("FAILED", page_path, str(exc) or exc.__class__.__name__)

    return {"candidates": candidates, "done": done, "failed": failed}


def call_glossary_llm(terms: list[str], tool_name: str) -> list[dict]:
    """Call Claude to generate canonical names and definitions for candidate terms.

    Returns list of {"term": str, "definition": str}.
    Requires ANTHROPIC_API_KEY in the environment.
    """
    import anthropic

    if not terms:
        return []

    client = anthropic.Anthropic()
    terms_text = "\n".join(f"- {t}" for t in terms)
    prompt = (
        f"You are a documentation glossary writer for {tool_name}.\n"
        "Given these domain terms, return a JSON array where each element has:\n"
        '- "term": canonical name (concise, properly capitalised)\n'
        '- "definition": tight one-sentence definition (≤20 words)\n\n'
        "Return only valid JSON array, nothing else.\n\n"
        f"Terms:\n{terms_text}"
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _strip_code_fences(response.content[0].text.strip())
    parsed = _parse_json_array_tolerant(text.strip())
    return [
        {"term": str(e.get("term", "")), "definition": str(e.get("definition", ""))}
        for e in parsed
        if isinstance(e, dict) and e.get("term") and e.get("definition")
    ]


def _glossary_has_entries(path: Path) -> bool:
    """Return True if an existing glossary.md already holds at least one term."""
    if not path.exists():
        return False
    return any(line.startswith("**") for line in path.read_text(encoding="utf-8").splitlines())


def gather_frontmatter_keywords(
    tool_name: str,
    base_dir: Path | None = None,
) -> list[str]:
    """Collect `keywords` from every Page's frontmatter under a Tool's bundle.

    Used as a fallback source of glossary candidate terms when a summarize pass
    re-processed no pages (everything cached) or wasn't run at all (a bare,
    incremental crawl), so no in-memory candidates exist. Skips the reserved
    navigation files (index/glossary/log).
    """
    if base_dir is None:
        base_dir = Path(".")
    tool_dir = base_dir / "docs" / "tools" / tool_name
    if not tool_dir.exists():
        return []

    reserved = {"index.md", "glossary.md", "log.md"}
    terms: list[str] = []
    for md_path in sorted(tool_dir.rglob("*.md")):
        if md_path.name in reserved:
            continue
        fm = read_page_frontmatter(md_path)
        kws = fm.get("keywords")
        if isinstance(kws, list):
            terms.extend(str(k).strip() for k in kws if str(k).strip())
    return terms


def generate_glossary(
    tool_name: str,
    candidates: list[str],
    base_dir: Path | None = None,
) -> Path:
    """Write docs/tools/<tool>/glossary.md from candidate terms.

    The Glossary is an OKF Concept: it carries frontmatter with
    `type: Glossary` and a title. Deduplicates candidates (case-insensitive),
    calls the LLM to generate canonical names and definitions, then writes the
    consolidated term list as the body. Returns the path written.

    Non-destructive: if no entries can be produced (no candidates, missing
    ANTHROPIC_API_KEY, or an API error) but a populated glossary already exists
    on disk, the existing file is left untouched rather than clobbered with an
    empty stub — so a bare/incremental re-crawl never wipes a glossary built by
    an earlier summarized run.
    """
    if base_dir is None:
        base_dir = Path(".")

    output_path = base_dir / "docs" / "tools" / tool_name / "glossary.md"

    seen: set[str] = set()
    unique: list[str] = []
    for term in candidates:
        key = term.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(term.strip())

    try:
        entries = call_glossary_llm(unique, tool_name)
    except Exception as exc:
        print(f"Warning: glossary generation skipped ({exc}).", flush=True)
        entries = []

    if not entries and _glossary_has_entries(output_path):
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = build_frontmatter_block(
        {"type": "Glossary", "title": f"{tool_name} Glossary"}
    )
    lines = [f"# {tool_name} Glossary", "", "<!-- auto-generated -->", ""]
    for entry in entries:
        lines.append(f"**{entry['term']}**: {entry['definition']}")
        lines.append("")

    output_path.write_text(frontmatter + "\n".join(lines), encoding="utf-8")
    return output_path


def _directory_title(rel_dir: str, tool_name: str) -> str:
    """Human-readable heading for an index.md in directory `rel_dir` (relative to bundle root)."""
    if rel_dir in ("", "."):
        return tool_name
    name = PurePosixPath(rel_dir).name
    return name.replace("-", " ").replace("_", " ").title()


def generate_indexes(
    tool_name: str,
    base_dir: Path | None = None,
    manifest: dict | None = None,
) -> list[Path]:
    """Write a reserved per-directory index.md into every directory of the bundle.

    Builds the directory tree in memory from the known page paths. Each
    index.md lists that directory's immediate child Concepts (with their
    `description` frontmatter, falling back to the title) and its immediate
    subdirectories. The bundle-root index.md carries an `okf_version: "0.1"`
    frontmatter block; nested index.md files have NO frontmatter. Returns the
    list of index.md paths written.
    """
    if base_dir is None:
        base_dir = Path(".")
    if manifest is None:
        manifest = load_manifest(tool_name, base_dir)

    bundle_root = base_dir / "docs" / "tools" / tool_name

    # Map each directory (POSIX rel path, "" = root) to its immediate child
    # concept files and child subdirectories.
    child_files: dict[str, set[str]] = defaultdict(set)
    subdirs: dict[str, set[str]] = defaultdict(set)
    all_dirs: set[str] = {""}

    for entry in manifest.values():
        page_path = entry.get("page_path", "")
        if not page_path:
            continue
        parts = page_path.split("/")
        # Register every ancestor directory.
        for i in range(len(parts)):
            parent = "/".join(parts[:i])
            all_dirs.add(parent)
            if i < len(parts) - 1:
                child = "/".join(parts[: i + 1])
                subdirs[parent].add(child)
        parent_dir = "/".join(parts[:-1])
        child_files[parent_dir].add(page_path)

    def description_for(page_path: str) -> tuple[str, str]:
        """Return (title, description) for a concept page from its frontmatter."""
        md_path = bundle_root / (page_path + ".md")
        title = PurePosixPath(page_path).name
        description = ""
        if md_path.exists():
            fm = read_page_frontmatter(md_path)
            title = fm.get("title") or title
            description = fm.get("description") or ""
        return title, description

    written: list[Path] = []
    for rel_dir in sorted(all_dirs):
        dir_path = bundle_root if rel_dir == "" else bundle_root / rel_dir
        dir_path.mkdir(parents=True, exist_ok=True)

        lines = [f"# {_directory_title(rel_dir, tool_name)}", ""]

        for page_path in sorted(child_files.get(rel_dir, ())):
            name = PurePosixPath(page_path).name
            title, description = description_for(page_path)
            suffix = f" - {description}" if description else " - "
            lines.append(f"- [{title}](./{name}.md){suffix}")

        for sub in sorted(subdirs.get(rel_dir, ())):
            name = PurePosixPath(sub).name
            label = _directory_title(sub, tool_name)
            lines.append(f"- [{label}](./{name}/) - ")

        body = "\n".join(lines) + "\n"
        if rel_dir == "":
            body = build_frontmatter_block({"okf_version": "0.1"}) + body

        index_path = dir_path / "index.md"
        index_path.write_text(body, encoding="utf-8")
        written.append(index_path)

    return written


def update_tools_map(
    tool_name: str,
    entry_url: str,
    base_dir: Path | None = None,
) -> Path:
    """Idempotently update docs/tools/index.md with this Tool's entry.

    This is the reserved listing for the tools collection (NO frontmatter):
    one entry per Tool linking to its bundle directory. Creates the file if
    absent. Replaces an existing entry for tool_name (matched by leading
    '- [{tool_name}]') or appends a new one. Returns the path written.
    """
    if base_dir is None:
        base_dir = Path(".")

    output_path = base_dir / "docs" / "tools" / "index.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists():
        content = output_path.read_text(encoding="utf-8")
    else:
        content = "# Tools\n"

    tool_dir = f"{tool_name}/"
    entry_line = f"- [{tool_name}](./{tool_dir}) - {entry_url}"
    tool_marker = f"- [{tool_name}]"

    lines = content.splitlines()
    new_lines: list[str] = []
    found = False
    for line in lines:
        if line.startswith(tool_marker):
            new_lines.append(entry_line)
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(entry_line)

    output_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return output_path


def _concept_label(page_path: str, base_dir: Path, tool_name: str) -> str:
    """Markdown link + title for a concept page, used in the log."""
    md_path = base_dir / "docs" / "tools" / tool_name / (page_path + ".md")
    title = PurePosixPath(page_path).name
    if md_path.exists():
        fm = read_page_frontmatter(md_path)
        title = fm.get("title") or title
    return f"[{title}](./{page_path}.md)"


def generate_log(
    tool_name: str,
    created: list[str],
    updated: list[str],
    base_dir: Path | None = None,
    today: str | None = None,
) -> Path:
    """Write/merge docs/tools/<tool>/log.md change history (newest first, NO frontmatter).

    `created` and `updated` are lists of page paths for newly-added and
    content-changed Concepts respectively. Ensures a `## <YYYY-MM-DD>` section
    for today exists with `**Creation**:` / `**Update**:` entries, merging into
    an existing section for today rather than duplicating it.
    """
    if base_dir is None:
        base_dir = Path(".")
    if today is None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")

    output_path = base_dir / "docs" / "tools" / tool_name / "log.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    new_entries: list[str] = []
    for pp in created:
        new_entries.append(f"- **Creation**: {_concept_label(pp, base_dir, tool_name)}")
    for pp in updated:
        new_entries.append(f"- **Update**: {_concept_label(pp, base_dir, tool_name)}")

    heading = f"## {today}"

    if not output_path.exists():
        lines = ["# Update Log", ""]
        if new_entries:
            lines.append(heading)
            lines.extend(new_entries)
            lines.append("")
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output_path

    if not new_entries:
        return output_path

    # Parse existing sections so we can merge today's without duplicating.
    text = output_path.read_text(encoding="utf-8")
    body_lines = text.splitlines()

    # Split into a preamble (title) and dated sections.
    sections: list[tuple[str, list[str]]] = []
    preamble: list[str] = []
    current_head: str | None = None
    current_body: list[str] = []
    for line in body_lines:
        if line.startswith("## "):
            if current_head is not None:
                sections.append((current_head, current_body))
            current_head = line
            current_body = []
        elif current_head is None:
            preamble.append(line)
        else:
            current_body.append(line)
    if current_head is not None:
        sections.append((current_head, current_body))

    # Merge into today's section if present (dedup by line), else prepend it.
    merged = False
    for i, (head, sec_body) in enumerate(sections):
        if head == heading:
            existing = {ln.strip() for ln in sec_body if ln.strip()}
            additions = [e for e in new_entries if e not in existing]
            kept = [ln for ln in sec_body if ln.strip()]
            sections[i] = (head, kept + additions + [""])
            merged = True
            break
    if not merged:
        sections.insert(0, (heading, new_entries + [""]))

    out: list[str] = []
    if preamble:
        # Drop trailing blank lines from preamble, then re-add a single spacer.
        while preamble and not preamble[-1].strip():
            preamble.pop()
        out.extend(preamble)
        out.append("")
    for head, sec_body in sections:
        out.append(head)
        out.extend([ln for ln in sec_body if ln.strip()])
        out.append("")

    output_path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    return output_path


def synthesize_site(
    tool_name: str,
    candidates: list[str],
    entry_url: str,
    base_dir: Path | None = None,
    manifest: dict | None = None,
    created: list[str] | None = None,
    updated: list[str] | None = None,
) -> dict:
    """Post-summarization synthesis: glossary, per-directory indexes, tools map, log.

    Returns {"glossary_path", "index_paths", "tools_map_path", "log_path"}.
    """
    if base_dir is None:
        base_dir = Path(".")

    glossary_path = generate_glossary(tool_name, candidates, base_dir)
    index_paths = generate_indexes(tool_name, base_dir, manifest)
    tools_map_path = update_tools_map(tool_name, entry_url, base_dir)
    log_path = generate_log(tool_name, created or [], updated or [], base_dir)

    return {
        "glossary_path": glossary_path,
        "index_paths": index_paths,
        "tools_map_path": tools_map_path,
        "log_path": log_path,
    }


def summarize_site(
    tool_name: str,
    base_dir: Path | None = None,
    concurrency: int = 6,
    batch_size: int = 15,
    progress: "Progress | None" = None,
) -> dict:
    """Fan out page summarization across all pages for a tool.

    Batches pages by URL-path directory and dispatches up to `concurrency`
    workers concurrently. Each worker calls the LLM to write summary and
    keywords into frontmatter and collects candidate glossary terms.

    progress, if provided, receives live per-page updates as workers complete
    (out of order, since the pool runs concurrently); None stays silent.

    Returns {"candidates": list[str], "done": int, "failed": int}.
    """
    if base_dir is None:
        base_dir = Path(".")

    manifest = load_manifest(tool_name, base_dir)
    if not manifest:
        return {"candidates": [], "done": 0, "failed": 0}

    batches = batch_pages_by_directory(list(manifest.keys()), batch_size=batch_size)

    prog = progress if progress is not None else _NULL_PROGRESS
    prog.start("Summarizing", sum(len(b) for b in batches))

    all_candidates: list[str] = []
    total_done = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(_summarize_batch, batch, manifest, tool_name, base_dir, prog)
            for batch in batches
        ]
        for future in futures:
            result = future.result()
            all_candidates.extend(result["candidates"])
            total_done += result["done"]
            total_failed += result["failed"]

    prog.done()

    return {"candidates": all_candidates, "done": total_done, "failed": total_failed}


def validate_bundle(bundle_dir: Path, base_dir: Path | None = None) -> int | None:
    """Run the vendored OKF validator against a bundle directory.

    Locates scripts/validate.sh next to this module, runs it on `bundle_dir`,
    streams its output, and returns the validator's exit code. Returns None
    (and prints a warning) if the validator script is missing — never crashes.
    """
    validator = Path(__file__).resolve().parent / "scripts" / "validate.sh"
    if not validator.exists():
        print(f"Warning: validator not found at {validator}; skipping validation.", flush=True)
        return None

    result = subprocess.run(
        ["bash", str(validator), str(bundle_dir)],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", flush=True)
    return result.returncode


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Crawl documentation pages to markdown.")
    parser.add_argument("url", help="Entry URL to crawl")
    parser.add_argument("--tool-name", help="Override tool name (defaults to URL host)")
    parser.add_argument("--single-page", action="store_true", help="Crawl only the given URL")
    parser.add_argument("--convert-only", action="store_true", help="Rebuild markdown from cache without fetching")
    parser.add_argument("--fresh", action="store_true", help="Wipe Tool cache and output, then re-crawl")
    parser.add_argument("--render", action="store_true", help="Force Playwright browser rendering for all pages")
    parser.add_argument("--safe-mode", action="store_true", help="Honor robots.txt (ignored by default)")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="KEY:VALUE",
        help="Extra request header (repeatable); e.g. --header 'Authorization: Bearer token'",
    )
    parser.add_argument(
        "--cookie",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Cookie to send (repeatable); e.g. --cookie 'session=abc'",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=_FETCH_DELAY,
        metavar="SECONDS",
        help=f"Inter-request delay in seconds (default: {_FETCH_DELAY})",
    )
    parser.add_argument("--show-info", action="store_true", help="Print derived tool_name and scope as JSON, then exit")
    parser.add_argument("--summarize", action="store_true", help="Summarize crawled pages via LLM after crawling")
    parser.add_argument(
        "--summarize-concurrency",
        type=int,
        default=6,
        metavar="N",
        help="Max concurrent summarization workers (default: 6)",
    )
    parser.add_argument(
        "--summarize-batch-size",
        type=int,
        default=15,
        metavar="N",
        help="Pages per summarization batch (default: 15)",
    )
    parser.add_argument(
        "--synthesize",
        action="store_true",
        help="Generate glossary, indexes, tools map, and log after crawling/summarizing",
    )
    parser.add_argument(
        "--type",
        default="Reference",
        metavar="TYPE",
        help='OKF `type` field written to each page frontmatter (default: "Reference")',
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip running the OKF validator on each written bundle",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress live progress on stderr (progress is on by default)",
    )
    args = parser.parse_args()

    if args.show_info:
        tool = args.tool_name or derive_tool_name(args.url)
        netloc, path_prefix = derive_crawl_scope(args.url)
        print(json.dumps({"tool_name": tool, "scope_host": netloc, "scope_prefix": path_prefix}))
        return

    extra_headers: dict = {}
    for h in args.header:
        key, _, value = h.partition(":")
        extra_headers[key.strip()] = value.strip()
    if args.cookie:
        extra_headers["Cookie"] = "; ".join(args.cookie)

    if args.single_page:
        result = crawl_page(
            args.url,
            tool_name=args.tool_name,
            force_render=args.render,
            extra_headers=extra_headers or None,
            type_=args.type,
        )
        if result:
            print(f"Written: {result}")
        else:
            print("Skipped (unchanged)")
        return

    tool = args.tool_name or derive_tool_name(args.url)
    progress = Progress(enabled=not args.quiet)
    errors: list = []
    diff: dict = {}
    paths = crawl_site(
        args.url,
        tool_name=args.tool_name,
        convert_only=args.convert_only,
        fresh=args.fresh,
        force_render=args.render,
        safe_mode=args.safe_mode,
        extra_headers=extra_headers or None,
        fetch_delay=args.delay,
        errors=errors,
        type_=args.type,
        diff=diff,
        progress=progress,
    )
    for p in paths:
        print(f"Written: {p}")
    if errors:
        print(f"\nFailed pages ({len(errors)}):")
        for err in errors:
            print(f"  {err['url']}: {err['error']}")

    candidates: list[str] = []
    if args.summarize:
        tally = summarize_site(
            tool,
            concurrency=args.summarize_concurrency,
            batch_size=args.summarize_batch_size,
            progress=progress,
        )
        candidates = tally["candidates"]
        print(f"\nSummarized {tally['done']} pages ({tally['failed']} failed).")

    # candidates are collected in-memory only during a live summarize pass; on a
    # fully-cached re-run nothing is re-summarized, and a bare/incremental crawl
    # never summarizes at all. In both cases fall back to the keywords already
    # persisted in each page's frontmatter so synthesis can still (re)build the
    # glossary instead of overwriting it with an empty stub.
    if not candidates:
        candidates = gather_frontmatter_keywords(tool)
    if candidates:
        print(f"Candidate terms: {', '.join(sorted(set(candidates))[:20])}")

    # Synthesis builds the reserved OKF files (per-directory index.md, glossary,
    # tools map, log) so the bundle is structurally valid. Always run it after a
    # crawl/convert; glossary regeneration is non-destructive (it preserves an
    # existing populated glossary when no entries can be produced).
    progress.banner("Synthesizing navigation layer…")
    synthesis = synthesize_site(
        tool,
        candidates,
        args.url,
        created=diff.get("created"),
        updated=diff.get("updated"),
    )
    print(f"Glossary:  {synthesis['glossary_path']}")
    print(f"Indexes:   {len(synthesis['index_paths'])} index.md files")
    print(f"Tools map: {synthesis['tools_map_path']}")
    print(f"Log:       {synthesis['log_path']}")

    # Validate each bundle written this run; exit non-zero if any fail.
    if not args.no_validate:
        bundle_dir = Path(".") / "docs" / "tools" / tool
        progress.banner("Validating bundle…")
        print(f"\nValidating bundle: {bundle_dir}")
        code = validate_bundle(bundle_dir)
        if code is not None and code != 0:
            sys.exit(code)


if __name__ == "__main__":
    main()
