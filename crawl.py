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

_HEADERS = {"User-Agent": "docs-to-md/1.0 (+https://github.com/NickMoignard/docs_to_md)"}

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
    """Fetch a page's fully-rendered HTML via headless Chromium."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(extra_http_headers=_HEADERS)
            page.goto(url, wait_until="networkidle", timeout=60000)
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
    """Convert URL path to a relative file path (no leading slash, no .md extension)."""
    parsed_path = urlparse(url).path
    path = parsed_path.strip("/")
    if not path:
        return "index"
    if parsed_path.endswith("/"):
        return path + "/index"
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
    Like derive_page_path, but promotes a URL to <path>/index when it has
    child pages in all_urls (i.e. it is both a page and a parent directory).
    """
    base_path = derive_page_path(url)
    if base_path.endswith("/index"):
        return base_path

    url_path = urlparse(url).path.rstrip("/") or "/"
    has_children = any(
        urlparse(other).path.startswith(url_path + "/")
        for other in all_urls
        if other != url
    )
    if not has_children:
        return base_path
    if base_path == "index":
        return "index"
    return base_path + "/index"


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

    frontmatter_text = build_frontmatter(
        title, url, entry.get("fetched_at", ""), entry.get("content_hash", ""), nav_path=nav_path
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
) -> list[str]:
    """BFS discovery of all in-scope pages starting from entry_url."""
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


def build_frontmatter(
    title: str,
    source_url: str,
    fetched_at: str,
    content_hash: str,
    nav_path: list[str] | None = None,
) -> str:
    """Build YAML frontmatter block."""
    data = {
        "title": title,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "content_hash": content_hash,
    }
    if nav_path:
        data["nav_path"] = nav_path
    return "---\n" + yaml.dump(data, default_flow_style=False, allow_unicode=True) + "---\n\n"


def crawl_page(
    url: str,
    tool_name: str | None = None,
    base_dir: Path | None = None,
    page_path: str | None = None,
    manifest: dict | None = None,
    force_render: bool = False,
    extra_headers: dict | None = None,
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
        html = fetch_with_playwright(url)
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
        html = fetch_with_playwright(url)
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

    frontmatter = build_frontmatter(title, url, fetched_at, content_hash, nav_path=nav_path)
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
) -> list[Path]:
    """
    Crawl all in-scope pages from entry_url and write each as markdown.

    Discovery uses sitemap.xml when present, BFS link-following otherwise.
    Crawl scope is auto-derived as same host + path prefix of entry_url.
    Pages that are parents of other pages are written as <path>/index.md.

    Args:
        convert_only: Rebuild markdown from the Raw-page cache without any network fetches.
        fresh: Wipe the Tool's cache and output before crawling.
        safe_mode: Honor the site's robots.txt (ignored by default per ADR-0002).
        extra_headers: Additional request headers passed to every fetch (auth, cookies, etc.).
        fetch_delay: Seconds to sleep between page fetches (default: 1.0).
        errors: If provided, dicts with "url" and "error" are appended for each failed page.

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

    if convert_only:
        written = []
        for url, entry in manifest.items():
            result = convert_from_cache(url, entry, tool_name, base_dir, manifest=manifest)
            if result is not None:
                written.append(result)
        return written

    robots = fetch_robots(entry_url) if safe_mode else None
    scope = derive_crawl_scope(entry_url)
    raw_urls = fetch_sitemap_urls(entry_url, scope) or _bfs_discover(
        entry_url, scope,
        safe_mode=safe_mode,
        robots=robots,
        extra_headers=extra_headers,
        fetch_delay=fetch_delay,
    )

    if safe_mode and robots:
        urls = [u for u in raw_urls if is_allowed_by_robots(u, robots)]
    else:
        urls = raw_urls

    written = []
    for i, url in enumerate(urls):
        if i > 0 and fetch_delay > 0:
            time.sleep(fetch_delay)
        path = compute_output_path(url, urls)
        result = crawl_page(
            url,
            tool_name=tool_name,
            base_dir=base_dir,
            page_path=path,
            manifest=manifest,
            force_render=force_render,
            extra_headers=extra_headers,
        )
        if result is not None:
            written.append(result)
        elif errors is not None:
            entry = manifest.get(url, {})
            if entry.get("status") == "errored":
                errors.append({"url": url, "error": entry.get("error", "unknown")})

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
    """Return True if the page's frontmatter contains a non-empty summary."""
    return bool(read_page_frontmatter(path).get("summary"))


def _strip_code_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text


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
) -> dict:
    """Summarize a batch of pages by calling the LLM and writing results to frontmatter.

    Skips pages whose frontmatter content_hash matches the manifest (unchanged) and
    that already have a non-empty summary. Returns candidates, done count, failed count.
    """
    candidates: list[str] = []
    done = 0
    failed = 0

    for url in urls:
        entry = manifest.get(url)
        if not entry or not entry.get("page_path"):
            failed += 1
            continue

        page_path = entry["page_path"]
        md_path = base_dir / "docs" / "tools" / tool_name / (page_path + ".md")
        if not md_path.exists():
            failed += 1
            continue

        fm = read_page_frontmatter(md_path)
        if fm.get("summary") and fm.get("content_hash") == entry.get("content_hash"):
            continue

        try:
            content = md_path.read_text(encoding="utf-8")
            result = call_summarize_llm(content, url)
            write_page_frontmatter(md_path, {
                "summary": result["summary"],
                "keywords": result["keywords"],
            })
            candidates.extend(result["candidates"])
            done += 1
        except Exception:
            failed += 1

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
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _strip_code_fences(response.content[0].text.strip())
    parsed = json.loads(text.strip())
    return [
        {"term": str(e.get("term", "")), "definition": str(e.get("definition", ""))}
        for e in parsed
        if e.get("term") and e.get("definition")
    ]


def generate_glossary(
    tool_name: str,
    candidates: list[str],
    base_dir: Path | None = None,
) -> Path:
    """Write docs/tools/<tool>/CONTEXT.md from candidate terms.

    Deduplicates candidates (case-insensitive), calls the LLM to generate
    canonical names and definitions, then writes the Glossary.
    Marks the file <!-- auto-generated -->. Returns the path written.
    """
    if base_dir is None:
        base_dir = Path(".")

    seen: set[str] = set()
    unique: list[str] = []
    for term in candidates:
        key = term.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(term.strip())

    entries = call_glossary_llm(unique, tool_name)

    output_path = base_dir / "docs" / "tools" / tool_name / "CONTEXT.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"# {tool_name} Glossary", "", "<!-- auto-generated -->", ""]
    for entry in entries:
        lines.append(f"**{entry['term']}**: {entry['definition']}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def generate_page_map(
    tool_name: str,
    base_dir: Path | None = None,
    manifest: dict | None = None,
) -> Path:
    """Write docs/tools/<tool>/_index.md as a URL-path tree with summaries.

    Reads each Page's frontmatter for its one-line summary, sorts by URL path,
    and emits an indented list. Marks the file <!-- auto-generated -->.
    Returns the path written.
    """
    if base_dir is None:
        base_dir = Path(".")
    if manifest is None:
        manifest = load_manifest(tool_name, base_dir)

    entries: list[tuple[str, str, str]] = []
    for url, entry in manifest.items():
        page_path = entry.get("page_path", "")
        md_path = base_dir / "docs" / "tools" / tool_name / (page_path + ".md")
        summary = ""
        if md_path.exists():
            fm = read_page_frontmatter(md_path)
            summary = fm.get("summary", "")
        url_path = urlparse(url).path or "/"
        entries.append((url_path, page_path, summary))

    entries.sort(key=lambda e: e[0])

    lines = [f"# {tool_name} — Page Map", "", "<!-- auto-generated -->", ""]
    for url_path, page_path, summary in entries:
        depth = max(0, url_path.rstrip("/").count("/") - 1)
        indent = "  " * depth
        rel_link = f"{page_path}.md"
        if summary:
            lines.append(f"{indent}- [{url_path}]({rel_link}) — {summary}")
        else:
            lines.append(f"{indent}- [{url_path}]({rel_link})")

    output_path = base_dir / "docs" / "tools" / tool_name / "_index.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def update_tools_map(
    tool_name: str,
    entry_url: str,
    base_dir: Path | None = None,
) -> Path:
    """Idempotently update docs/tools/CONTEXT-MAP.md with this Tool's entry.

    Creates the file if absent. Replaces an existing entry for tool_name
    (matched by leading '- **{tool_name}**') or appends a new one.
    Returns the path written.
    """
    if base_dir is None:
        base_dir = Path(".")

    output_path = base_dir / "docs" / "tools" / "CONTEXT-MAP.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists():
        content = output_path.read_text(encoding="utf-8")
    else:
        content = "# Tools Map\n\n<!-- auto-generated -->\n"

    tool_dir = f"{tool_name}/"
    entry_line = f"- **{tool_name}** — [{tool_dir}]({tool_dir})"
    tool_marker = f"- **{tool_name}**"

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


def synthesize_site(
    tool_name: str,
    candidates: list[str],
    entry_url: str,
    base_dir: Path | None = None,
    manifest: dict | None = None,
) -> dict:
    """Post-summarization synthesis: glossary, page map, and tools map.

    Returns {"glossary_path": Path, "page_map_path": Path, "tools_map_path": Path}.
    """
    if base_dir is None:
        base_dir = Path(".")

    glossary_path = generate_glossary(tool_name, candidates, base_dir)
    page_map_path = generate_page_map(tool_name, base_dir, manifest)
    tools_map_path = update_tools_map(tool_name, entry_url, base_dir)

    return {
        "glossary_path": glossary_path,
        "page_map_path": page_map_path,
        "tools_map_path": tools_map_path,
    }


def summarize_site(
    tool_name: str,
    base_dir: Path | None = None,
    concurrency: int = 6,
    batch_size: int = 15,
) -> dict:
    """Fan out page summarization across all pages for a tool.

    Batches pages by URL-path directory and dispatches up to `concurrency`
    workers concurrently. Each worker calls the LLM to write summary and
    keywords into frontmatter and collects candidate glossary terms.

    Returns {"candidates": list[str], "done": int, "failed": int}.
    """
    if base_dir is None:
        base_dir = Path(".")

    manifest = load_manifest(tool_name, base_dir)
    if not manifest:
        return {"candidates": [], "done": 0, "failed": 0}

    batches = batch_pages_by_directory(list(manifest.keys()), batch_size=batch_size)

    all_candidates: list[str] = []
    total_done = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(_summarize_batch, batch, manifest, tool_name, base_dir)
            for batch in batches
        ]
        for future in futures:
            result = future.result()
            all_candidates.extend(result["candidates"])
            total_done += result["done"]
            total_failed += result["failed"]

    return {"candidates": all_candidates, "done": total_done, "failed": total_failed}


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
        help="Generate glossary, page map, and tools map after crawling/summarizing",
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
        )
        if result:
            print(f"Written: {result}")
        else:
            print("Skipped (unchanged)")
    else:
        errors: list = []
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
        )
        for p in paths:
            print(f"Written: {p}")
        if errors:
            print(f"\nFailed pages ({len(errors)}):")
            for err in errors:
                print(f"  {err['url']}: {err['error']}")

        candidates: list[str] = []
        if args.summarize or args.synthesize:
            tool = args.tool_name or derive_tool_name(args.url)

            if args.summarize:
                tally = summarize_site(
                    tool,
                    concurrency=args.summarize_concurrency,
                    batch_size=args.summarize_batch_size,
                )
                candidates = tally["candidates"]
                print(f"\nSummarized {tally['done']} pages ({tally['failed']} failed).")
                if candidates:
                    print(f"Candidate terms: {', '.join(sorted(set(candidates))[:20])}")

            if args.synthesize:
                synthesis = synthesize_site(tool, candidates, args.url)
                print(f"Glossary:  {synthesis['glossary_path']}")
                print(f"Page map:  {synthesis['page_map_path']}")
                print(f"Tools map: {synthesis['tools_map_path']}")


if __name__ == "__main__":
    main()
