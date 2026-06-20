"""Tests for crawl.py — single-page and multi-page crawl to markdown."""
import hashlib
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.robotparser import RobotFileParser

import pytest
import requests
import yaml
from bs4 import BeautifulSoup

from crawl import (
    _CONTENT_THRESHOLD,
    _fetch_with_retry,
    _summarize_batch,
    batch_pages_by_directory,
    build_frontmatter,
    cache_raw_page,
    call_glossary_llm,
    call_summarize_llm,
    compute_output_path,
    convert_from_cache,
    crawl_page,
    crawl_site,
    derive_crawl_scope,
    derive_page_path,
    derive_tool_name,
    ensure_playwright_chromium,
    extract_content,
    extract_nav_path,
    extract_page_links,
    fetch_robots,
    fetch_sitemap_urls,
    fetch_with_playwright,
    generate_glossary,
    generate_page_map,
    is_allowed_by_robots,
    is_content_below_threshold,
    is_in_scope,
    is_page_summarized,
    load_manifest,
    main,
    make_images_absolute,
    manifest_path,
    preprocess_code_blocks,
    raw_cache_dir,
    read_page_frontmatter,
    rewrite_links,
    save_manifest,
    summarize_site,
    synthesize_site,
    to_markdown,
    update_tools_map,
    wipe_tool_data,
    write_page_frontmatter,
)


class TestDeriveToolName:
    def test_strips_docs_subdomain(self):
        assert derive_tool_name("https://docs.stripe.com/payments") == "stripe"

    def test_strips_www_prefix(self):
        assert derive_tool_name("https://www.python.org/docs") == "python"

    def test_plain_domain(self):
        assert derive_tool_name("https://reactjs.org/docs/hello-world") == "reactjs"

    def test_strips_api_subdomain(self):
        assert derive_tool_name("https://api.openai.com/docs") == "openai"

    def test_strips_developer_subdomain(self):
        assert derive_tool_name("https://developer.mozilla.org/en-US/docs") == "mozilla"

    def test_no_subdomain(self):
        assert derive_tool_name("https://fastapi.tiangolo.com") == "fastapi"


class TestDerivePagePath:
    def test_simple_path(self):
        assert derive_page_path("https://docs.stripe.com/payments/charges") == "payments/charges"

    def test_root_path(self):
        assert derive_page_path("https://docs.stripe.com/") == "index"

    def test_no_path(self):
        assert derive_page_path("https://docs.stripe.com") == "index"

    def test_trailing_slash_is_index(self):
        assert derive_page_path("https://docs.stripe.com/payments/") == "payments/index"

    def test_single_segment(self):
        assert derive_page_path("https://docs.stripe.com/payments") == "payments"

    def test_deep_path(self):
        assert derive_page_path("https://docs.stripe.com/api/charges/create") == "api/charges/create"


class TestExtractContent:
    def test_extracts_from_main_element(self):
        html = """<html><body>
            <nav>Nav stuff</nav>
            <main><h1>Title</h1><p>Content here</p></main>
            <footer>Footer stuff</footer>
        </body></html>"""
        title, content = extract_content(html)
        assert "Content here" in content
        assert "Nav stuff" not in content
        assert "Footer stuff" not in content

    def test_extracts_from_role_main(self):
        html = """<html><body>
            <div role="navigation">Nav</div>
            <div role="main"><p>Main content</p></div>
        </body></html>"""
        _, content = extract_content(html)
        assert "Main content" in content
        assert "Nav" not in content

    def test_extracts_from_article(self):
        html = """<html><body>
            <header>Header</header>
            <article><p>Article body</p></article>
        </body></html>"""
        _, content = extract_content(html)
        assert "Article body" in content
        assert "Header" not in content

    def test_strips_nav_chrome(self):
        html = """<html><body>
            <main>
                <nav>Sidebar nav</nav>
                <p>Real content</p>
                <footer>Footer inside main</footer>
            </main>
        </body></html>"""
        _, content = extract_content(html)
        assert "Real content" in content
        assert "Sidebar nav" not in content
        assert "Footer inside main" not in content

    def test_strips_toc(self):
        html = """<html><body>
            <main>
                <div class="toc">Table of contents</div>
                <p>Actual content</p>
            </main>
        </body></html>"""
        _, content = extract_content(html)
        assert "Actual content" in content
        assert "Table of contents" not in content

    def test_extracts_title_from_h1(self):
        html = """<html><head><title>Page Title - Docs</title></head><body>
            <main><h1>Real H1 Title</h1><p>Content</p></main>
        </body></html>"""
        title, _ = extract_content(html)
        assert title == "Real H1 Title"

    def test_falls_back_to_page_title(self):
        html = """<html><head><title>Page Title</title></head><body>
            <main><p>Content without h1</p></main>
        </body></html>"""
        title, _ = extract_content(html)
        assert title == "Page Title"

    def test_strips_copy_buttons(self):
        html = """<html><body><main>
            <button class="copy-button">Copy</button>
            <p>Real content</p>
        </main></body></html>"""
        _, content = extract_content(html)
        assert "Real content" in content
        assert "Copy" not in content


class TestBuildFrontmatter:
    def test_contains_required_fields(self):
        fm = build_frontmatter(
            title="Test Page",
            source_url="https://docs.example.com/page",
            fetched_at="2026-06-20T12:00:00+00:00",
            content_hash="abc123",
        )
        assert "title: Test Page" in fm
        assert "source_url: https://docs.example.com/page" in fm
        assert "fetched_at:" in fm
        assert "content_hash: abc123" in fm

    def test_has_yaml_delimiters(self):
        fm = build_frontmatter("T", "https://x.com", "2026-01-01", "hash")
        assert fm.startswith("---\n")
        assert "---\n" in fm[4:]


class TestToMarkdown:
    def test_converts_heading(self):
        html = "<h1>Hello World</h1>"
        md = to_markdown(html)
        assert "# Hello World" in md

    def test_converts_paragraph(self):
        html = "<p>Simple paragraph</p>"
        md = to_markdown(html)
        assert "Simple paragraph" in md

    def test_converts_code_block(self):
        html = "<pre><code>print('hello')</code></pre>"
        md = to_markdown(html)
        assert "print('hello')" in md


class TestCrawlPage:
    SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Charges - Stripe Docs</title></head>
<body>
  <nav>Site navigation</nav>
  <main>
    <h1>Charges</h1>
    <p>The Charge object represents a payment. Charge objects are created whenever you charge a credit or debit card or redirect a customer to checkout via Stripe. Use the charges API to create, capture, or reverse charges, and to retrieve information about individual charges.</p>
    <p>Each Charge has a unique identifier and a set of properties such as amount, currency, status, and payment method details. You can list all charges, retrieve a specific charge, and update or capture it as needed.</p>
  </main>
  <footer>Footer content</footer>
</body>
</html>"""

    @pytest.fixture
    def mock_get(self):
        response = MagicMock()
        response.text = self.SAMPLE_HTML
        with patch("crawl.requests.get", return_value=response):
            yield

    def test_creates_output_file(self, tmp_path, mock_get):
        result = crawl_page(
            "https://docs.stripe.com/payments/charges",
            tool_name="stripe",
            base_dir=tmp_path,
        )
        assert result.exists()
        assert result == tmp_path / "docs/tools/stripe/payments/charges.md"

    def test_output_has_frontmatter(self, tmp_path, mock_get):
        result = crawl_page(
            "https://docs.stripe.com/payments/charges",
            tool_name="stripe",
            base_dir=tmp_path,
        )
        content = result.read_text()
        assert content.startswith("---\n")
        assert "source_url:" in content
        assert "fetched_at:" in content
        assert "content_hash:" in content

    def test_output_contains_main_content(self, tmp_path, mock_get):
        result = crawl_page(
            "https://docs.stripe.com/payments/charges",
            tool_name="stripe",
            base_dir=tmp_path,
        )
        content = result.read_text()
        assert "Charge object" in content
        assert "Site navigation" not in content
        assert "Footer content" not in content

    def test_derives_tool_name_from_url(self, tmp_path, mock_get):
        result = crawl_page(
            "https://docs.stripe.com/payments/charges",
            base_dir=tmp_path,
        )
        assert "stripe" in str(result)

    def test_content_hash_matches_html(self, tmp_path, mock_get):
        result = crawl_page(
            "https://docs.stripe.com/payments/charges",
            tool_name="stripe",
            base_dir=tmp_path,
        )
        content = result.read_text()
        expected_hash = hashlib.sha256(self.SAMPLE_HTML.encode()).hexdigest()
        assert expected_hash in content


class TestDeriveCrawlScope:
    def test_path_url(self):
        assert derive_crawl_scope("https://docs.stripe.com/payments") == ("docs.stripe.com", "/payments")

    def test_root_with_slash(self):
        assert derive_crawl_scope("https://docs.stripe.com/") == ("docs.stripe.com", "/")

    def test_no_path(self):
        assert derive_crawl_scope("https://docs.stripe.com") == ("docs.stripe.com", "/")

    def test_deep_path(self):
        assert derive_crawl_scope("https://docs.stripe.com/payments/charges") == (
            "docs.stripe.com",
            "/payments/charges",
        )


class TestIsInScope:
    def test_child_url_in_scope(self):
        scope = ("docs.stripe.com", "/payments")
        assert is_in_scope("https://docs.stripe.com/payments/charges", scope)

    def test_entry_url_in_scope(self):
        scope = ("docs.stripe.com", "/payments")
        assert is_in_scope("https://docs.stripe.com/payments", scope)

    def test_different_host_not_in_scope(self):
        scope = ("docs.stripe.com", "/payments")
        assert not is_in_scope("https://stripe.com/payments/charges", scope)

    def test_different_path_prefix_not_in_scope(self):
        scope = ("docs.stripe.com", "/payments")
        assert not is_in_scope("https://docs.stripe.com/api/charges", scope)

    def test_root_scope_includes_all(self):
        scope = ("docs.stripe.com", "/")
        assert is_in_scope("https://docs.stripe.com/anything/here", scope)

    def test_partial_path_not_in_scope(self):
        scope = ("docs.stripe.com", "/payments")
        assert not is_in_scope("https://docs.stripe.com/payment-links", scope)


class TestExtractPageLinks:
    def test_extracts_absolute_in_scope_links(self):
        html = """<html><body>
            <a href="https://docs.stripe.com/payments/charges">Charges</a>
            <a href="https://docs.stripe.com/api">API</a>
        </body></html>"""
        scope = ("docs.stripe.com", "/payments")
        links = extract_page_links(html, "https://docs.stripe.com/payments", scope)
        assert "https://docs.stripe.com/payments/charges" in links
        assert "https://docs.stripe.com/api" not in links

    def test_resolves_relative_links(self):
        html = """<html><body>
            <a href="/payments/charges">Charges</a>
        </body></html>"""
        scope = ("docs.stripe.com", "/payments")
        links = extract_page_links(html, "https://docs.stripe.com/payments", scope)
        assert "https://docs.stripe.com/payments/charges" in links

    def test_strips_fragments(self):
        html = """<html><body>
            <a href="/payments/charges#section1">Charges</a>
        </body></html>"""
        scope = ("docs.stripe.com", "/payments")
        links = extract_page_links(html, "https://docs.stripe.com/payments", scope)
        assert "https://docs.stripe.com/payments/charges" in links
        assert "https://docs.stripe.com/payments/charges#section1" not in links

    def test_excludes_external_links(self):
        html = """<html><body>
            <a href="https://external.com/page">External</a>
        </body></html>"""
        scope = ("docs.stripe.com", "/payments")
        links = extract_page_links(html, "https://docs.stripe.com/payments", scope)
        assert "https://external.com/page" not in links

    def test_deduplicates_links(self):
        html = """<html><body>
            <a href="/payments/charges">Charges</a>
            <a href="/payments/charges">Charges again</a>
        </body></html>"""
        scope = ("docs.stripe.com", "/payments")
        links = extract_page_links(html, "https://docs.stripe.com/payments", scope)
        assert links.count("https://docs.stripe.com/payments/charges") == 1


class TestFetchSitemapUrls:
    SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://docs.stripe.com/payments</loc></url>
    <url><loc>https://docs.stripe.com/payments/charges</loc></url>
    <url><loc>https://stripe.com/other</loc></url>
</urlset>"""

    def test_returns_in_scope_urls(self):
        scope = ("docs.stripe.com", "/payments")
        with patch("crawl.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = self.SITEMAP_XML
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            urls = fetch_sitemap_urls("https://docs.stripe.com/payments", scope)
        assert "https://docs.stripe.com/payments" in urls
        assert "https://docs.stripe.com/payments/charges" in urls
        assert "https://stripe.com/other" not in urls

    def test_returns_none_on_http_error(self):
        with patch("crawl.requests.get") as mock_get:
            mock_get.side_effect = requests.HTTPError()
            result = fetch_sitemap_urls("https://docs.stripe.com/payments", ("docs.stripe.com", "/"))
        assert result is None

    def test_returns_none_on_connection_error(self):
        with patch("crawl.requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError()
            result = fetch_sitemap_urls("https://docs.stripe.com/payments", ("docs.stripe.com", "/"))
        assert result is None


class TestComputeOutputPath:
    def test_leaf_page_unchanged(self):
        urls = [
            "https://docs.stripe.com/payments",
            "https://docs.stripe.com/payments/charges",
        ]
        assert compute_output_path("https://docs.stripe.com/payments/charges", urls) == "payments/charges"

    def test_parent_page_becomes_index(self):
        urls = [
            "https://docs.stripe.com/payments",
            "https://docs.stripe.com/payments/charges",
        ]
        assert compute_output_path("https://docs.stripe.com/payments", urls) == "payments/index"

    def test_only_page_stays_leaf(self):
        urls = ["https://docs.stripe.com/payments"]
        assert compute_output_path("https://docs.stripe.com/payments", urls) == "payments"

    def test_trailing_slash_already_index(self):
        urls = [
            "https://docs.stripe.com/payments/",
            "https://docs.stripe.com/payments/charges",
        ]
        assert compute_output_path("https://docs.stripe.com/payments/", urls) == "payments/index"

    def test_root_page_with_children_is_index(self):
        urls = [
            "https://docs.stripe.com/",
            "https://docs.stripe.com/payments",
        ]
        assert compute_output_path("https://docs.stripe.com/", urls) == "index"


class TestCrawlSite:
    ENTRY_HTML = """<!DOCTYPE html>
<html><head><title>Payments - Stripe</title></head>
<body><main>
    <h1>Payments</h1>
    <p>Payment overview.</p>
    <a href="/payments/charges">Charges</a>
</main></body></html>"""

    CHARGES_HTML = """<!DOCTYPE html>
<html><head><title>Charges - Stripe</title></head>
<body><main>
    <h1>Charges</h1>
    <p>Charge objects.</p>
</main></body></html>"""

    SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://docs.stripe.com/payments</loc></url>
    <url><loc>https://docs.stripe.com/payments/charges</loc></url>
</urlset>"""

    def _make_mock(self, url, **kwargs):
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status = MagicMock()
        mock.headers = MagicMock()
        mock.headers.get = lambda key, default=None: None
        if "sitemap" in url:
            raise requests.HTTPError()
        elif url.rstrip("/") == "https://docs.stripe.com/payments":
            mock.text = self.ENTRY_HTML
        else:
            mock.text = self.CHARGES_HTML
        return mock

    def test_bfs_crawls_multiple_pages(self, tmp_path):
        with patch("crawl.requests.get", side_effect=self._make_mock):
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                fetch_delay=0,
            )
        assert len(paths) >= 2
        assert tmp_path / "docs/tools/stripe/payments/index.md" in paths
        assert tmp_path / "docs/tools/stripe/payments/charges.md" in paths

    def test_sitemap_used_when_present(self, tmp_path):
        def mock_get_fn(url, **kwargs):
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.headers = MagicMock()
            mock.headers.get = lambda key, default=None: None
            if "sitemap" in url:
                mock.text = self.SITEMAP_XML
            elif url.rstrip("/") == "https://docs.stripe.com/payments":
                mock.text = self.ENTRY_HTML
            else:
                mock.text = self.CHARGES_HTML
            return mock

        with patch("crawl.requests.get", side_effect=mock_get_fn):
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                fetch_delay=0,
            )
        assert len(paths) == 2
        assert tmp_path / "docs/tools/stripe/payments/index.md" in paths
        assert tmp_path / "docs/tools/stripe/payments/charges.md" in paths

    def test_out_of_scope_links_not_crawled(self, tmp_path):
        html_with_external = """<!DOCTYPE html>
<html><head><title>Payments</title></head>
<body><main>
    <h1>Payments</h1>
    <a href="/payments/charges">Charges</a>
    <a href="/api/charges">API (out of scope)</a>
</main></body></html>"""

        def mock_get_fn(url, **kwargs):
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.headers = MagicMock()
            mock.headers.get = lambda key, default=None: None
            if "sitemap" in url:
                raise requests.HTTPError()
            elif url.rstrip("/") == "https://docs.stripe.com/payments":
                mock.text = html_with_external
            else:
                mock.text = self.CHARGES_HTML
            return mock

        with patch("crawl.requests.get", side_effect=mock_get_fn):
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                fetch_delay=0,
            )
        written_paths = [str(p) for p in paths]
        assert not any("api" in p for p in written_paths)

    def test_derives_tool_name_from_url(self, tmp_path):
        with patch("crawl.requests.get", side_effect=self._make_mock):
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                base_dir=tmp_path,
                fetch_delay=0,
            )
        assert all("stripe" in str(p) for p in paths)


# ---------------------------------------------------------------------------
# Issue 3: Raw cache, Manifest & incremental re-crawl
# ---------------------------------------------------------------------------


class TestRawCacheDir:
    def test_returns_correct_path(self, tmp_path):
        assert raw_cache_dir("stripe", tmp_path) == tmp_path / ".cache" / "tools" / "stripe" / "raw"

    def test_defaults_to_cwd(self):
        result = raw_cache_dir("stripe")
        assert result == Path(".") / ".cache" / "tools" / "stripe" / "raw"


class TestManifestPath:
    def test_returns_correct_path(self, tmp_path):
        assert manifest_path("stripe", tmp_path) == tmp_path / ".cache" / "tools" / "stripe" / "manifest.json"

    def test_defaults_to_cwd(self):
        result = manifest_path("stripe")
        assert result == Path(".") / ".cache" / "tools" / "stripe" / "manifest.json"


class TestLoadManifest:
    def test_returns_empty_dict_when_missing(self, tmp_path):
        assert load_manifest("stripe", tmp_path) == {}

    def test_loads_existing_manifest(self, tmp_path):
        data = {"https://docs.stripe.com/payments": {"page_path": "payments", "content_hash": "abc"}}
        path = manifest_path("stripe", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        assert load_manifest("stripe", tmp_path) == data


class TestSaveManifest:
    def test_writes_json_to_disk(self, tmp_path):
        data = {"https://docs.stripe.com/payments": {"page_path": "payments", "content_hash": "abc"}}
        save_manifest(data, "stripe", tmp_path)
        path = manifest_path("stripe", tmp_path)
        assert path.exists()
        assert json.loads(path.read_text()) == data

    def test_creates_parent_dirs(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        assert manifest_path("stripe", tmp_path).exists()

    def test_roundtrip(self, tmp_path):
        data = {"url": {"page_path": "p", "etag": None, "content_hash": "x"}}
        save_manifest(data, "stripe", tmp_path)
        assert load_manifest("stripe", tmp_path) == data


class TestCacheRawPage:
    def test_writes_html_to_correct_path(self, tmp_path):
        result = cache_raw_page("<html>hi</html>", "payments/charges", "stripe", tmp_path)
        expected = tmp_path / ".cache" / "tools" / "stripe" / "raw" / "payments" / "charges.html"
        assert result == expected
        assert result.read_text() == "<html>hi</html>"

    def test_creates_parent_dirs(self, tmp_path):
        cache_raw_page("<html/>", "a/b/c", "stripe", tmp_path)
        assert (tmp_path / ".cache" / "tools" / "stripe" / "raw" / "a" / "b" / "c.html").exists()


class TestWipeToolData:
    def test_removes_cache_directory(self, tmp_path):
        cache = tmp_path / ".cache" / "tools" / "stripe"
        cache.mkdir(parents=True)
        (cache / "manifest.json").write_text("{}")
        wipe_tool_data("stripe", tmp_path)
        assert not cache.exists()

    def test_removes_output_directory(self, tmp_path):
        output = tmp_path / "docs" / "tools" / "stripe"
        output.mkdir(parents=True)
        (output / "page.md").write_text("# Page")
        wipe_tool_data("stripe", tmp_path)
        assert not output.exists()

    def test_no_error_when_dirs_absent(self, tmp_path):
        wipe_tool_data("stripe", tmp_path)  # must not raise

    def test_only_wipes_named_tool(self, tmp_path):
        other = tmp_path / ".cache" / "tools" / "twilio"
        other.mkdir(parents=True)
        (other / "manifest.json").write_text("{}")
        wipe_tool_data("stripe", tmp_path)
        assert other.exists()


class TestCrawlPageCache:
    SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Charges - Stripe Docs</title></head>
<body>
  <nav>Site navigation</nav>
  <main>
    <h1>Charges</h1>
    <p>The Charge object represents a payment. Charge objects are created whenever you charge a credit or debit card or redirect a customer to checkout via Stripe. Use the charges API to create, capture, or reverse charges, and to retrieve information about individual charges.</p>
    <p>Each Charge has a unique identifier and a set of properties such as amount, currency, status, and payment method details. You can list all charges, retrieve a specific charge, and update or capture it as needed.</p>
  </main>
  <footer>Footer content</footer>
</body>
</html>"""

    def _mock_response(self, html=None, status_code=200, etag=None, last_modified=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = html or self.SAMPLE_HTML
        resp.raise_for_status = MagicMock()
        resp.headers = MagicMock()
        resp.headers.get = lambda key, default=None: {
            "ETag": etag,
            "Last-Modified": last_modified,
        }.get(key, default)
        return resp

    def test_saves_raw_html_to_cache(self, tmp_path):
        with patch("crawl.requests.get", return_value=self._mock_response()):
            crawl_page("https://docs.stripe.com/payments/charges", tool_name="stripe", base_dir=tmp_path)
        cache_file = tmp_path / ".cache" / "tools" / "stripe" / "raw" / "payments" / "charges.html"
        assert cache_file.exists()
        assert cache_file.read_text(encoding="utf-8") == self.SAMPLE_HTML

    def test_updates_manifest_entry(self, tmp_path):
        manifest = {}
        with patch("crawl.requests.get", return_value=self._mock_response(etag='"v1"', last_modified="Fri, 20 Jun 2026 00:00:00 GMT")):
            crawl_page(
                "https://docs.stripe.com/payments/charges",
                tool_name="stripe",
                base_dir=tmp_path,
                manifest=manifest,
            )
        url = "https://docs.stripe.com/payments/charges"
        assert url in manifest
        entry = manifest[url]
        assert entry["page_path"] == "payments/charges"
        assert "fetched_at" in entry
        assert "content_hash" in entry
        assert entry["etag"] == '"v1"'
        assert entry["last_modified"] == "Fri, 20 Jun 2026 00:00:00 GMT"
        assert "cache_path" in entry

    def test_returns_none_on_304(self, tmp_path):
        with patch("crawl.requests.get", return_value=self._mock_response(status_code=304)):
            result = crawl_page(
                "https://docs.stripe.com/payments/charges",
                tool_name="stripe",
                base_dir=tmp_path,
            )
        assert result is None

    def test_skips_when_content_hash_unchanged(self, tmp_path):
        html = self.SAMPLE_HTML
        content_hash = hashlib.sha256(html.encode()).hexdigest()
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "etag": None,
                "last_modified": None,
                "content_hash": content_hash,
            }
        }
        with patch("crawl.requests.get", return_value=self._mock_response(html=html)):
            result = crawl_page(
                "https://docs.stripe.com/payments/charges",
                tool_name="stripe",
                base_dir=tmp_path,
                manifest=manifest,
            )
        assert result is None
        assert not (tmp_path / "docs/tools/stripe/payments/charges.md").exists()

    def test_sends_etag_validator(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "etag": '"v1"',
                "last_modified": None,
                "content_hash": "different_hash",
            }
        }
        with patch("crawl.requests.get", return_value=self._mock_response()) as mock_get:
            crawl_page(
                "https://docs.stripe.com/payments/charges",
                tool_name="stripe",
                base_dir=tmp_path,
                manifest=manifest,
            )
        headers = mock_get.call_args[1]["headers"]
        assert headers.get("If-None-Match") == '"v1"'

    def test_sends_last_modified_validator(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "etag": None,
                "last_modified": "Fri, 20 Jun 2026 00:00:00 GMT",
                "content_hash": "different_hash",
            }
        }
        with patch("crawl.requests.get", return_value=self._mock_response()) as mock_get:
            crawl_page(
                "https://docs.stripe.com/payments/charges",
                tool_name="stripe",
                base_dir=tmp_path,
                manifest=manifest,
            )
        headers = mock_get.call_args[1]["headers"]
        assert headers.get("If-Modified-Since") == "Fri, 20 Jun 2026 00:00:00 GMT"

    def test_refetches_when_hash_changed(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "etag": None,
                "last_modified": None,
                "content_hash": "old_hash",
            }
        }
        with patch("crawl.requests.get", return_value=self._mock_response()):
            result = crawl_page(
                "https://docs.stripe.com/payments/charges",
                tool_name="stripe",
                base_dir=tmp_path,
                manifest=manifest,
            )
        assert result is not None
        assert result.exists()


class TestConvertFromCache:
    SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Charges - Stripe Docs</title></head>
<body><main><h1>Charges</h1><p>The Charge object.</p></main></body>
</html>"""

    def _setup_cache(self, tmp_path):
        cache_path = tmp_path / ".cache" / "tools" / "stripe" / "raw" / "payments" / "charges.html"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(self.SAMPLE_HTML, encoding="utf-8")
        return cache_path

    def test_writes_markdown_from_cache(self, tmp_path):
        cache_path = self._setup_cache(tmp_path)
        entry = {
            "page_path": "payments/charges",
            "cache_path": str(cache_path.relative_to(tmp_path)),
            "fetched_at": "2026-06-20T00:00:00+00:00",
            "content_hash": "abc123",
        }
        result = convert_from_cache("https://docs.stripe.com/payments/charges", entry, "stripe", tmp_path)
        assert result is not None
        assert result == tmp_path / "docs/tools/stripe/payments/charges.md"
        assert result.exists()

    def test_output_contains_content(self, tmp_path):
        cache_path = self._setup_cache(tmp_path)
        entry = {
            "page_path": "payments/charges",
            "cache_path": str(cache_path.relative_to(tmp_path)),
            "fetched_at": "2026-06-20T00:00:00+00:00",
            "content_hash": "abc123",
        }
        result = convert_from_cache("https://docs.stripe.com/payments/charges", entry, "stripe", tmp_path)
        text = result.read_text()
        assert "Charge object" in text
        assert "source_url:" in text

    def test_returns_none_when_cache_missing(self, tmp_path):
        entry = {
            "page_path": "payments/charges",
            "cache_path": ".cache/tools/stripe/raw/payments/charges.html",
            "fetched_at": "2026-06-20T00:00:00+00:00",
            "content_hash": "abc123",
        }
        result = convert_from_cache("https://docs.stripe.com/payments/charges", entry, "stripe", tmp_path)
        assert result is None


class TestCrawlSiteIncremental:
    ENTRY_HTML = """<!DOCTYPE html>
<html><head><title>Payments - Stripe</title></head>
<body><main>
    <h1>Payments</h1>
    <p>Payment overview.</p>
    <a href="/payments/charges">Charges</a>
</main></body></html>"""

    CHARGES_HTML = """<!DOCTYPE html>
<html><head><title>Charges - Stripe</title></head>
<body><main>
    <h1>Charges</h1>
    <p>Charge objects.</p>
</main></body></html>"""

    def _make_mock(self, url, **kwargs):
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status = MagicMock()
        mock.headers = MagicMock()
        mock.headers.get = lambda key, default=None: None
        if "sitemap" in url:
            raise requests.HTTPError()
        elif url.rstrip("/") == "https://docs.stripe.com/payments":
            mock.text = self.ENTRY_HTML
        else:
            mock.text = self.CHARGES_HTML
        return mock

    def test_saves_manifest_after_crawl(self, tmp_path):
        with patch("crawl.requests.get", side_effect=self._make_mock):
            crawl_site("https://docs.stripe.com/payments", tool_name="stripe", base_dir=tmp_path, fetch_delay=0)
        manifest = load_manifest("stripe", tmp_path)
        assert len(manifest) >= 1
        some_url = next(iter(manifest))
        assert "page_path" in manifest[some_url]
        assert "content_hash" in manifest[some_url]

    def test_convert_only_no_network(self, tmp_path):
        # Pre-populate cache and manifest
        cache_path = tmp_path / ".cache" / "tools" / "stripe" / "raw" / "payments" / "charges.html"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(self.CHARGES_HTML, encoding="utf-8")
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "cache_path": str(cache_path.relative_to(tmp_path)),
                "fetched_at": "2026-06-20T00:00:00+00:00",
                "content_hash": hashlib.sha256(self.CHARGES_HTML.encode()).hexdigest(),
                "etag": None,
                "last_modified": None,
            }
        }
        save_manifest(manifest, "stripe", tmp_path)

        with patch("crawl.requests.get") as mock_get:
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                convert_only=True,
            )

        mock_get.assert_not_called()
        assert len(paths) == 1
        assert paths[0] == tmp_path / "docs/tools/stripe/payments/charges.md"

    def test_fresh_wipes_before_crawl(self, tmp_path):
        # Existing stale cache and output
        old_cache = tmp_path / ".cache" / "tools" / "stripe" / "old.json"
        old_cache.parent.mkdir(parents=True, exist_ok=True)
        old_cache.write_text("{}")
        old_output = tmp_path / "docs" / "tools" / "stripe" / "stale.md"
        old_output.parent.mkdir(parents=True, exist_ok=True)
        old_output.write_text("# Stale")

        with patch("crawl.requests.get", side_effect=self._make_mock):
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                fresh=True,
                fetch_delay=0,
            )

        assert not old_cache.exists()
        assert not old_output.exists()
        assert len(paths) >= 1

    def test_incremental_skips_304_pages(self, tmp_path):
        charges_hash = hashlib.sha256(self.CHARGES_HTML.encode()).hexdigest()
        # Pre-populate manifest so charges page has an ETag
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "cache_path": ".cache/tools/stripe/raw/payments/charges.html",
                "fetched_at": "2026-06-20T00:00:00+00:00",
                "content_hash": charges_hash,
                "etag": '"v1"',
                "last_modified": None,
            }
        }
        save_manifest(manifest, "stripe", tmp_path)

        call_count = 0

        def mock_get_fn(url, **kwargs):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            mock.raise_for_status = MagicMock()
            mock.headers = MagicMock()
            mock.headers.get = lambda key, default=None: None
            if "sitemap" in url:
                raise requests.HTTPError()
            elif url.rstrip("/") == "https://docs.stripe.com/payments":
                mock.status_code = 200
                mock.text = self.ENTRY_HTML
            else:
                # Charges page: return 304 (not modified)
                mock.status_code = 304
                mock.text = ""
            return mock

        with patch("crawl.requests.get", side_effect=mock_get_fn):
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                fetch_delay=0,
            )

        # payments/index should be written, charges should be skipped (304)
        written_names = [p.name for p in paths]
        assert "index.md" in written_names
        assert "charges.md" not in written_names


# ---------------------------------------------------------------------------
# Issue 5: Link rewriting, images & nav_path metadata
# ---------------------------------------------------------------------------


class TestExtractNavPath:
    def test_returns_empty_when_no_nav(self):
        html = "<html><body><main><p>Content</p></main></body></html>"
        assert extract_nav_path(html, "https://docs.stripe.com/payments") == []

    def test_extracts_group_label_from_aria_current(self):
        html = """<html><body>
            <nav class="sidebar">
                <h3>Payments</h3>
                <ul><li><a href="/payments/charges" aria-current="page">Charges</a></li></ul>
            </nav>
            <main><p>Content</p></main>
        </body></html>"""
        result = extract_nav_path(html, "https://docs.stripe.com/payments/charges")
        assert result == ["Payments"]

    def test_extracts_group_label_by_url_match(self):
        html = """<html><body>
            <nav>
                <h3>API Reference</h3>
                <ul><li><a href="/api/charges">Charges</a></li></ul>
            </nav>
        </body></html>"""
        result = extract_nav_path(html, "https://docs.stripe.com/api/charges")
        assert result == ["API Reference"]

    def test_returns_empty_when_no_matching_link(self):
        html = """<html><body>
            <nav>
                <h3>Other Section</h3>
                <ul><li><a href="/other/page">Other</a></li></ul>
            </nav>
        </body></html>"""
        result = extract_nav_path(html, "https://docs.stripe.com/payments/charges")
        assert result == []

    def test_extracts_from_active_class(self):
        html = """<html><body>
            <nav>
                <h3>Guides</h3>
                <ul><li><a href="/guides/quickstart" class="active">Quickstart</a></li></ul>
            </nav>
        </body></html>"""
        result = extract_nav_path(html, "https://docs.stripe.com/guides/quickstart")
        assert result == ["Guides"]

    def test_returns_empty_list_for_unmatched_url(self):
        html = """<html><body>
            <nav><ul><li><a href="/other">Other</a></li></ul></nav>
        </body></html>"""
        assert extract_nav_path(html, "https://docs.stripe.com/payments") == []


class TestRewriteLinks:
    def test_rewrites_in_manifest_link(self):
        soup = BeautifulSoup('<p><a href="https://docs.stripe.com/payments/charges">link</a></p>', "html.parser")
        manifest = {"https://docs.stripe.com/payments/charges": {"page_path": "payments/charges"}}
        rewrite_links(soup, "https://docs.stripe.com/payments", manifest, "payments/index")
        assert soup.find("a")["href"] == "charges.md"

    def test_keeps_external_link_absolute(self):
        soup = BeautifulSoup('<a href="https://external.com/page">link</a>', "html.parser")
        rewrite_links(soup, "https://docs.stripe.com/payments", {}, "payments/index")
        assert soup.find("a")["href"] == "https://external.com/page"

    def test_keeps_same_page_anchor(self):
        soup = BeautifulSoup('<a href="#section">link</a>', "html.parser")
        rewrite_links(soup, "https://docs.stripe.com/payments", {}, "payments/index")
        assert soup.find("a")["href"] == "#section"

    def test_preserves_anchor_in_rewritten_link(self):
        soup = BeautifulSoup('<a href="https://docs.stripe.com/payments/charges#section">link</a>', "html.parser")
        manifest = {"https://docs.stripe.com/payments/charges": {"page_path": "payments/charges"}}
        rewrite_links(soup, "https://docs.stripe.com/payments", manifest, "payments/index")
        assert soup.find("a")["href"] == "charges.md#section"

    def test_keeps_not_in_manifest_link_absolute(self):
        soup = BeautifulSoup('<a href="https://docs.stripe.com/api/charges">link</a>', "html.parser")
        rewrite_links(soup, "https://docs.stripe.com/payments", {}, "payments/index")
        assert soup.find("a")["href"] == "https://docs.stripe.com/api/charges"

    def test_resolves_relative_link_and_rewrites_if_in_manifest(self):
        soup = BeautifulSoup('<a href="/payments/charges">link</a>', "html.parser")
        manifest = {"https://docs.stripe.com/payments/charges": {"page_path": "payments/charges"}}
        rewrite_links(soup, "https://docs.stripe.com/payments", manifest, "payments/index")
        assert soup.find("a")["href"] == "charges.md"

    def test_relative_path_across_sections(self):
        soup = BeautifulSoup('<a href="https://docs.stripe.com/api/intro">link</a>', "html.parser")
        manifest = {"https://docs.stripe.com/api/intro": {"page_path": "api/intro"}}
        rewrite_links(soup, "https://docs.stripe.com/payments", manifest, "payments/index")
        assert soup.find("a")["href"] == "../api/intro.md"

    def test_root_page_link(self):
        soup = BeautifulSoup('<a href="https://docs.stripe.com/payments/charges">link</a>', "html.parser")
        manifest = {"https://docs.stripe.com/payments/charges": {"page_path": "payments/charges"}}
        rewrite_links(soup, "https://docs.stripe.com/", manifest, "index")
        assert soup.find("a")["href"] == "payments/charges.md"


class TestMakeImagesAbsolute:
    def test_makes_relative_src_absolute(self):
        soup = BeautifulSoup('<img src="/images/logo.png" alt="logo">', "html.parser")
        make_images_absolute(soup, "https://docs.stripe.com/payments")
        assert soup.find("img")["src"] == "https://docs.stripe.com/images/logo.png"

    def test_keeps_absolute_src_unchanged(self):
        soup = BeautifulSoup('<img src="https://cdn.example.com/img.png" alt="x">', "html.parser")
        make_images_absolute(soup, "https://docs.stripe.com/payments")
        assert soup.find("img")["src"] == "https://cdn.example.com/img.png"

    def test_keeps_data_uri_unchanged(self):
        soup = BeautifulSoup('<img src="data:image/png;base64,abc">', "html.parser")
        make_images_absolute(soup, "https://docs.stripe.com/payments")
        assert soup.find("img")["src"] == "data:image/png;base64,abc"


class TestBuildFrontmatterNavPath:
    def test_includes_nav_path_when_provided(self):
        fm = build_frontmatter("T", "https://x.com", "2026-01-01", "hash", nav_path=["Payments", "Charges"])
        assert "nav_path:" in fm

    def test_omits_nav_path_when_none(self):
        fm = build_frontmatter("T", "https://x.com", "2026-01-01", "hash", nav_path=None)
        assert "nav_path" not in fm

    def test_omits_nav_path_when_empty_list(self):
        fm = build_frontmatter("T", "https://x.com", "2026-01-01", "hash", nav_path=[])
        assert "nav_path" not in fm


class TestCrawlPageIssue5:
    PAGE_HTML = """<!DOCTYPE html>
<html><head><title>Payments</title></head>
<body>
  <nav class="sidebar">
    <h3>Overview</h3>
    <ul><li><a href="/payments" aria-current="page">Payments</a></li></ul>
  </nav>
  <main>
    <h1>Payments</h1>
    <p>This payments overview page contains enough substantive prose to stay
    comfortably above the static-content threshold so the Playwright fallback
    is not triggered for these link-rewriting and metadata assertions.</p>
    <a href="/payments/charges">Charges</a>
    <a href="https://external.com">External</a>
    <img src="/images/logo.png" alt="logo">
  </main>
</body></html>"""

    def _mock_response(self, html):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.raise_for_status = MagicMock()
        resp.headers = MagicMock()
        resp.headers.get = lambda key, default=None: None
        return resp

    def test_rewrites_in_manifest_links(self, tmp_path):
        manifest = {"https://docs.stripe.com/payments/charges": {"page_path": "payments/charges"}}
        with patch("crawl.requests.get", return_value=self._mock_response(self.PAGE_HTML)):
            result = crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                page_path="payments/index",
                manifest=manifest,
            )
        content = result.read_text()
        assert "charges.md" in content
        assert "https://external.com" in content

    def test_makes_images_absolute_in_output(self, tmp_path):
        with patch("crawl.requests.get", return_value=self._mock_response(self.PAGE_HTML)):
            result = crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                page_path="payments/index",
                manifest={},
            )
        content = result.read_text()
        assert "https://docs.stripe.com/images/logo.png" in content

    def test_nav_path_in_frontmatter(self, tmp_path):
        with patch("crawl.requests.get", return_value=self._mock_response(self.PAGE_HTML)):
            result = crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                page_path="payments/index",
                manifest={},
            )
        content = result.read_text()
        assert "nav_path:" in content

    def test_no_link_rewriting_without_manifest(self, tmp_path):
        with patch("crawl.requests.get", return_value=self._mock_response(self.PAGE_HTML)):
            result = crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                page_path="payments/index",
            )
        content = result.read_text()
        # Without manifest, relative links are resolved to absolute (not rewritten to .md)
        assert "charges.md" not in content


class TestConvertFromCacheIssue5:
    SAMPLE_HTML = """<!DOCTYPE html>
<html><head><title>Charges</title></head>
<body><main>
    <h1>Charges</h1>
    <a href="https://docs.stripe.com/payments">Back to Payments</a>
</main></body></html>"""

    def _setup_cache(self, tmp_path, html=None):
        cache_path = tmp_path / ".cache/tools/stripe/raw/payments/charges.html"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(html or self.SAMPLE_HTML, encoding="utf-8")
        return cache_path

    def test_rewrites_links_when_manifest_provided(self, tmp_path):
        cache_path = self._setup_cache(tmp_path)
        entry = {
            "page_path": "payments/charges",
            "cache_path": str(cache_path.relative_to(tmp_path)),
            "fetched_at": "2026-06-20",
            "content_hash": "abc",
            "nav_path": [],
        }
        manifest = {
            "https://docs.stripe.com/payments/charges": entry,
            "https://docs.stripe.com/payments": {"page_path": "payments/index"},
        }
        result = convert_from_cache(
            "https://docs.stripe.com/payments/charges", entry, "stripe", tmp_path, manifest=manifest
        )
        content = result.read_text()
        assert "../payments/index.md" in content or "index.md" in content

    def test_nav_path_from_manifest_entry_in_frontmatter(self, tmp_path):
        cache_path = self._setup_cache(tmp_path)
        entry = {
            "page_path": "payments/charges",
            "cache_path": str(cache_path.relative_to(tmp_path)),
            "fetched_at": "2026-06-20",
            "content_hash": "abc",
            "nav_path": ["Payments"],
        }
        result = convert_from_cache(
            "https://docs.stripe.com/payments/charges", entry, "stripe", tmp_path
        )
        content = result.read_text()
        assert "nav_path:" in content

    def test_no_nav_path_when_not_in_entry(self, tmp_path):
        cache_path = self._setup_cache(tmp_path)
        entry = {
            "page_path": "payments/charges",
            "cache_path": str(cache_path.relative_to(tmp_path)),
            "fetched_at": "2026-06-20",
            "content_hash": "abc",
        }
        result = convert_from_cache(
            "https://docs.stripe.com/payments/charges", entry, "stripe", tmp_path
        )
        content = result.read_text()
        assert "nav_path" not in content


# ---------------------------------------------------------------------------
# Issue 6: Playwright fallback
# ---------------------------------------------------------------------------


class TestIsContentBelowThreshold:
    def test_empty_string_is_below(self):
        assert is_content_below_threshold("") is True

    def test_whitespace_only_is_below(self):
        assert is_content_below_threshold("   \n\t  ") is True

    def test_content_below_threshold_returns_true(self):
        assert is_content_below_threshold("x" * (_CONTENT_THRESHOLD - 1)) is True

    def test_content_at_threshold_returns_false(self):
        assert is_content_below_threshold("x" * _CONTENT_THRESHOLD) is False

    def test_rich_content_returns_false(self):
        assert is_content_below_threshold("word " * 100) is False

    def test_custom_threshold(self):
        assert is_content_below_threshold("hello", threshold=10) is True
        assert is_content_below_threshold("hello world!!", threshold=10) is False


@pytest.mark.playwright
class TestEnsurePlaywrightChromium:
    def test_no_install_when_binary_exists(self, tmp_path):
        """When chromium executable exists, subprocess should not be called."""
        fake_exec = tmp_path / "chromium"
        fake_exec.write_text("binary")

        with patch("playwright.sync_api.sync_playwright") as mock_pw, \
             patch("crawl.subprocess") as mock_sub:
            mock_ctx = MagicMock()
            mock_pw.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_pw.return_value.__exit__ = MagicMock(return_value=False)
            mock_ctx.chromium.executable_path = str(fake_exec)
            ensure_playwright_chromium()

        mock_sub.run.assert_not_called()

    def test_installs_when_binary_missing(self, tmp_path):
        """When chromium executable is absent, subprocess.run must be called."""
        missing_path = str(tmp_path / "no_such_file")

        with patch("playwright.sync_api.sync_playwright") as mock_pw, \
             patch("crawl.subprocess") as mock_sub:
            mock_ctx = MagicMock()
            mock_pw.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_pw.return_value.__exit__ = MagicMock(return_value=False)
            mock_ctx.chromium.executable_path = missing_path
            ensure_playwright_chromium()

        mock_sub.run.assert_called_once()
        cmd = mock_sub.run.call_args[0][0]
        assert "playwright" in " ".join(cmd)
        assert "install" in cmd
        assert "chromium" in cmd


@pytest.mark.playwright
class TestFetchWithPlaywright:
    def test_returns_rendered_html(self):
        rendered_html = "<html><body><main><p>Rendered content</p></main></body></html>"

        with patch("playwright.sync_api.sync_playwright") as mock_pw:
            mock_ctx = MagicMock()
            mock_pw.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_pw.return_value.__exit__ = MagicMock(return_value=False)
            mock_browser = MagicMock()
            mock_page = MagicMock()
            mock_page.content.return_value = rendered_html
            mock_browser.new_page.return_value = mock_page
            mock_ctx.chromium.launch.return_value = mock_browser

            result = fetch_with_playwright("https://docs.example.com/page")

        assert result == rendered_html
        mock_page.goto.assert_called_once()
        mock_browser.close.assert_called_once()

    def test_browser_closed_even_on_error(self):
        with patch("playwright.sync_api.sync_playwright") as mock_pw:
            mock_ctx = MagicMock()
            mock_pw.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_pw.return_value.__exit__ = MagicMock(return_value=False)
            mock_browser = MagicMock()
            mock_page = MagicMock()
            mock_page.goto.side_effect = RuntimeError("timeout")
            mock_browser.new_page.return_value = mock_page
            mock_ctx.chromium.launch.return_value = mock_browser

            with pytest.raises(RuntimeError):
                fetch_with_playwright("https://docs.example.com/page")

        mock_browser.close.assert_called_once()


class TestCrawlPagePlaywrightFallback:
    SPARSE_HTML = """<!DOCTYPE html>
<html><head><title>Sparse - Docs</title></head>
<body><main><p>Hi</p></main></body></html>"""

    RICH_HTML = (
        "<!DOCTYPE html>\n<html><head><title>Rich - Docs</title></head>\n"
        "<body><main><h1>Rich page</h1>"
        + "<p>This is a paragraph with substantial content.</p>" * 20
        + "</main></body></html>"
    )

    def _mock_static(self, html):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.raise_for_status = MagicMock()
        resp.headers = MagicMock()
        resp.headers.get = lambda key, default=None: None
        return resp

    def test_rich_static_content_skips_playwright(self, tmp_path):
        with patch("crawl.requests.get", return_value=self._mock_static(self.RICH_HTML)), \
             patch("crawl.fetch_with_playwright") as mock_pw, \
             patch("crawl.ensure_playwright_chromium"):
            crawl_page("https://docs.example.com/page", base_dir=tmp_path)
        mock_pw.assert_not_called()

    def test_sparse_content_triggers_playwright_fallback(self, tmp_path):
        with patch("crawl.requests.get", return_value=self._mock_static(self.SPARSE_HTML)), \
             patch("crawl.fetch_with_playwright", return_value=self.RICH_HTML) as mock_pw, \
             patch("crawl.ensure_playwright_chromium") as mock_ensure:
            result = crawl_page("https://docs.example.com/page", base_dir=tmp_path)
        assert result is not None
        mock_pw.assert_called_once_with("https://docs.example.com/page")
        mock_ensure.assert_called_once()

    def test_force_render_behavior(self, tmp_path):
        with patch("crawl.requests.get") as mock_get, \
             patch("crawl.fetch_with_playwright", return_value=self.RICH_HTML) as mock_pw, \
             patch("crawl.ensure_playwright_chromium") as mock_ensure:
            crawl_page("https://docs.example.com/page", base_dir=tmp_path, force_render=True)
        mock_get.assert_not_called()
        mock_pw.assert_called_once_with("https://docs.example.com/page")
        mock_ensure.assert_called_once()

    def test_playwright_rendered_content_written_to_output(self, tmp_path):
        with patch("crawl.requests.get", return_value=self._mock_static(self.SPARSE_HTML)), \
             patch("crawl.fetch_with_playwright", return_value=self.RICH_HTML), \
             patch("crawl.ensure_playwright_chromium"):
            result = crawl_page("https://docs.example.com/page", base_dir=tmp_path)
        text = result.read_text()
        assert "Rich page" in text


class TestCrawlSiteForceRender:
    ENTRY_HTML = (
        "<!DOCTYPE html>\n<html><head><title>Payments</title></head>\n<body><main>\n"
        "<h1>Payments</h1>"
        + "<p>Payment overview paragraph with enough content.</p>" * 10
        + "\n<a href='/payments/charges'>Charges</a>\n</main></body></html>"
    )
    CHARGES_HTML = (
        "<!DOCTYPE html>\n<html><head><title>Charges</title></head>\n<body><main>\n"
        "<h1>Charges</h1>"
        + "<p>Charge objects paragraph with enough content.</p>" * 10
        + "\n</main></body></html>"
    )

    def test_force_render_passes_to_crawl_page(self, tmp_path):
        def fake_fetch_with_playwright(url):
            if "charges" in url:
                return self.CHARGES_HTML
            return self.ENTRY_HTML

        with patch("crawl.fetch_sitemap_urls", return_value=["https://docs.stripe.com/payments"]), \
             patch("crawl.fetch_with_playwright", side_effect=fake_fetch_with_playwright) as mock_pw, \
             patch("crawl.ensure_playwright_chromium"):
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                force_render=True,
            )

        assert len(paths) == 1
        mock_pw.assert_any_call("https://docs.stripe.com/payments")


# ---------------------------------------------------------------------------
# Issue 7: Crawl robustness & policy
# ---------------------------------------------------------------------------


class TestFetchWithRetry:
    def _ok_response(self, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.raise_for_status = MagicMock()
        return resp

    def test_returns_response_on_success(self):
        resp = self._ok_response()
        with patch("crawl.requests.get", return_value=resp):
            result = _fetch_with_retry("https://example.com", {})
        assert result is resp

    def test_retries_on_connection_error_then_succeeds(self):
        resp = self._ok_response()
        with patch("crawl.requests.get", side_effect=[requests.ConnectionError(), resp]) as mock_get:
            with patch("crawl.time.sleep"):
                result = _fetch_with_retry("https://example.com", {})
        assert result is resp
        assert mock_get.call_count == 2

    def test_retries_on_timeout_then_succeeds(self):
        resp = self._ok_response()
        with patch("crawl.requests.get", side_effect=[requests.Timeout(), resp]) as mock_get:
            with patch("crawl.time.sleep"):
                result = _fetch_with_retry("https://example.com", {})
        assert result is resp
        assert mock_get.call_count == 2

    def test_retries_on_transient_5xx_status(self):
        resp_500 = self._ok_response(500)
        resp_200 = self._ok_response(200)
        with patch("crawl.requests.get", side_effect=[resp_500, resp_200]) as mock_get:
            with patch("crawl.time.sleep"):
                result = _fetch_with_retry("https://example.com", {}, max_retries=1)
        assert result is resp_200
        assert mock_get.call_count == 2

    def test_retries_on_429_rate_limit(self):
        resp_429 = self._ok_response(429)
        resp_200 = self._ok_response(200)
        with patch("crawl.requests.get", side_effect=[resp_429, resp_200]) as mock_get:
            with patch("crawl.time.sleep"):
                result = _fetch_with_retry("https://example.com", {}, max_retries=1)
        assert result is resp_200
        assert mock_get.call_count == 2

    def test_raises_after_max_retries_connection_error(self):
        with patch("crawl.requests.get", side_effect=requests.ConnectionError("timeout")):
            with patch("crawl.time.sleep"):
                with pytest.raises(requests.ConnectionError):
                    _fetch_with_retry("https://example.com", {}, max_retries=2)

    def test_raises_after_max_retries_transient_status(self):
        resp_500 = self._ok_response(500)
        resp_500.raise_for_status = MagicMock(side_effect=requests.HTTPError("500"))
        with patch("crawl.requests.get", return_value=resp_500):
            with patch("crawl.time.sleep"):
                with pytest.raises(requests.HTTPError):
                    _fetch_with_retry("https://example.com", {}, max_retries=1)

    def test_no_retry_on_404(self):
        resp_404 = self._ok_response(404)
        with patch("crawl.requests.get", return_value=resp_404) as mock_get:
            result = _fetch_with_retry("https://example.com", {})
        assert result is resp_404
        assert mock_get.call_count == 1

    def test_sleeps_between_retries(self):
        resp = self._ok_response()
        with patch("crawl.requests.get", side_effect=[requests.ConnectionError(), resp]):
            with patch("crawl.time.sleep") as mock_sleep:
                _fetch_with_retry("https://example.com", {}, backoff_factor=1.5)
        mock_sleep.assert_called_once()


class TestFetchRobots:
    def test_returns_robot_parser(self):
        with patch.object(RobotFileParser, "read"):
            result = fetch_robots("https://docs.stripe.com/payments")
        assert isinstance(result, RobotFileParser)

    def test_handles_robots_fetch_error_by_allowing_all(self):
        with patch.object(RobotFileParser, "read", side_effect=OSError("network error")):
            rp = fetch_robots("https://docs.stripe.com/payments")
        assert rp.can_fetch("*", "https://docs.stripe.com/anything")


class TestIsAllowedByRobots:
    def _make_robots(self, rules: str) -> RobotFileParser:
        rp = RobotFileParser()
        rp.parse(rules.splitlines())
        return rp

    def test_allowed_when_no_restrictions(self):
        rp = self._make_robots("User-agent: *\nAllow: /\n")
        assert is_allowed_by_robots("https://docs.stripe.com/payments", rp)

    def test_disallowed_path_blocked(self):
        rp = self._make_robots("User-agent: *\nDisallow: /\n")
        assert not is_allowed_by_robots("https://docs.stripe.com/payments", rp)

    def test_specific_path_disallowed(self):
        rp = self._make_robots("User-agent: *\nDisallow: /private/\n")
        assert not is_allowed_by_robots("https://docs.stripe.com/private/secret", rp)
        assert is_allowed_by_robots("https://docs.stripe.com/public/page", rp)


class TestCrawlPageRobustness:
    SAMPLE_HTML = """<!DOCTYPE html>
<html><head><title>Page</title></head>
<body><main><h1>Title</h1><p>Content.</p></main></body>
</html>"""

    def _mock_response(self, status_code=200, html=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = html or self.SAMPLE_HTML
        resp.raise_for_status = MagicMock(
            side_effect=requests.HTTPError(f"{status_code}") if status_code >= 400 else None
        )
        resp.headers = MagicMock()
        resp.headers.get = lambda key, default=None: None
        return resp

    def test_extra_headers_merged_into_request(self, tmp_path):
        resp = self._mock_response()
        with patch("crawl.requests.get", return_value=resp) as mock_get:
            crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                extra_headers={"Authorization": "Bearer token123"},
            )
        called_headers = mock_get.call_args[1]["headers"]
        assert called_headers.get("Authorization") == "Bearer token123"
        assert "User-Agent" in called_headers

    def test_cookie_passthrough(self, tmp_path):
        resp = self._mock_response()
        with patch("crawl.requests.get", return_value=resp) as mock_get:
            crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                extra_headers={"Cookie": "session=abc123"},
            )
        called_headers = mock_get.call_args[1]["headers"]
        assert called_headers.get("Cookie") == "session=abc123"

    def test_permanent_failure_records_errored_in_manifest(self, tmp_path):
        resp = self._mock_response(status_code=403)
        manifest = {}
        with patch("crawl.requests.get", return_value=resp):
            result = crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                manifest=manifest,
            )
        assert result is None
        entry = manifest.get("https://docs.stripe.com/payments", {})
        assert entry.get("status") == "errored"
        assert "error" in entry

    def test_permanent_failure_returns_none(self, tmp_path):
        resp = self._mock_response(status_code=404)
        with patch("crawl.requests.get", return_value=resp):
            result = crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
            )
        assert result is None

    def test_connection_error_records_errored_in_manifest(self, tmp_path):
        manifest = {}
        with patch("crawl.requests.get", side_effect=requests.ConnectionError("refused")):
            with patch("crawl.time.sleep"):
                result = crawl_page(
                    "https://docs.stripe.com/payments",
                    tool_name="stripe",
                    base_dir=tmp_path,
                    manifest=manifest,
                )
        assert result is None
        entry = manifest.get("https://docs.stripe.com/payments", {})
        assert entry.get("status") == "errored"

    def test_no_output_file_on_permanent_failure(self, tmp_path):
        resp = self._mock_response(status_code=404)
        with patch("crawl.requests.get", return_value=resp):
            crawl_page(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
            )
        assert not (tmp_path / "docs/tools/stripe/payments.md").exists()


class TestCrawlSiteRobustness:
    ENTRY_HTML = """<!DOCTYPE html>
<html><head><title>Payments</title></head>
<body><main>
    <h1>Payments</h1>
    <a href="/payments/charges">Charges</a>
</main></body></html>"""

    CHARGES_HTML = """<!DOCTYPE html>
<html><head><title>Charges</title></head>
<body><main><h1>Charges</h1><p>Charge objects.</p></main></body></html>"""

    def _make_mock(self, url, **kwargs):
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status = MagicMock()
        mock.headers = MagicMock()
        mock.headers.get = lambda key, default=None: None
        if "sitemap" in url:
            raise requests.HTTPError()
        elif url.rstrip("/") == "https://docs.stripe.com/payments":
            mock.text = self.ENTRY_HTML
        else:
            mock.text = self.CHARGES_HTML
        return mock

    def test_delay_applied_between_pages(self, tmp_path):
        with patch("crawl.requests.get", side_effect=self._make_mock):
            with patch("crawl.time.sleep") as mock_sleep:
                crawl_site(
                    "https://docs.stripe.com/payments",
                    tool_name="stripe",
                    base_dir=tmp_path,
                    fetch_delay=0.5,
                )
        # At least one sleep between pages
        assert mock_sleep.call_count >= 1
        assert all(call.args[0] == 0.5 for call in mock_sleep.call_args_list if call.args)

    def test_crawl_continues_after_page_error(self, tmp_path):
        def mock_get_fn(url, **kwargs):
            mock = MagicMock()
            mock.raise_for_status = MagicMock()
            mock.headers = MagicMock()
            mock.headers.get = lambda key, default=None: None
            if "sitemap" in url:
                raise requests.HTTPError()
            elif url.rstrip("/") == "https://docs.stripe.com/payments":
                mock.status_code = 200
                mock.text = self.ENTRY_HTML
            else:
                # charges page: permanent 404
                mock.status_code = 404
                mock.raise_for_status = MagicMock(side_effect=requests.HTTPError("404"))
                mock.text = ""
            return mock

        with patch("crawl.requests.get", side_effect=mock_get_fn):
            paths = crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                fetch_delay=0,
            )
        # payments/index should be written even though charges failed
        assert any("index.md" in str(p) for p in paths)

    def test_errored_pages_tracked_in_errors_list(self, tmp_path):
        def mock_get_fn(url, **kwargs):
            mock = MagicMock()
            mock.raise_for_status = MagicMock()
            mock.headers = MagicMock()
            mock.headers.get = lambda key, default=None: None
            if "sitemap" in url:
                raise requests.HTTPError()
            elif url.rstrip("/") == "https://docs.stripe.com/payments":
                mock.status_code = 200
                mock.text = self.ENTRY_HTML
            else:
                mock.status_code = 404
                mock.raise_for_status = MagicMock(side_effect=requests.HTTPError("404"))
                mock.text = ""
            return mock

        errors = []
        with patch("crawl.requests.get", side_effect=mock_get_fn):
            crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                fetch_delay=0,
                errors=errors,
            )
        assert len(errors) == 1
        assert "https://docs.stripe.com/payments/charges" in errors[0]["url"]
        assert "error" in errors[0]

    def test_robots_txt_honored_in_safe_mode(self, tmp_path):
        rp = RobotFileParser()
        rp.parse("User-agent: *\nDisallow: /payments/charges\n".splitlines())

        def mock_get_fn(url, **kwargs):
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.headers = MagicMock()
            mock.headers.get = lambda key, default=None: None
            if "sitemap" in url:
                raise requests.HTTPError()
            mock.text = self.ENTRY_HTML
            return mock

        with patch("crawl.requests.get", side_effect=mock_get_fn):
            with patch("crawl.fetch_robots", return_value=rp):
                paths = crawl_site(
                    "https://docs.stripe.com/payments",
                    tool_name="stripe",
                    base_dir=tmp_path,
                    safe_mode=True,
                    fetch_delay=0,
                )
        # charges should be filtered out by robots.txt
        assert not any("charges" in str(p) for p in paths)

    def test_robots_txt_ignored_by_default(self, tmp_path):
        with patch("crawl.requests.get", side_effect=self._make_mock):
            with patch("crawl.fetch_robots") as mock_fetch_robots:
                crawl_site(
                    "https://docs.stripe.com/payments",
                    tool_name="stripe",
                    base_dir=tmp_path,
                    fetch_delay=0,
                )
        mock_fetch_robots.assert_not_called()

    def test_extra_headers_propagated_to_requests(self, tmp_path):
        captured = {}

        def mock_get_fn(url, **kwargs):
            captured[url] = kwargs.get("headers", {})
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.headers = MagicMock()
            mock.headers.get = lambda key, default=None: None
            if "sitemap" in url:
                raise requests.HTTPError()
            mock.text = self.ENTRY_HTML
            return mock

        with patch("crawl.requests.get", side_effect=mock_get_fn):
            crawl_site(
                "https://docs.stripe.com/payments",
                tool_name="stripe",
                base_dir=tmp_path,
                fetch_delay=0,
                extra_headers={"Authorization": "Bearer secret"},
            )
        # At least one request should carry the extra header
        assert any("Authorization" in h for h in captured.values())


class TestMainShowInfo:
    def test_outputs_json_with_tool_name_and_scope(self, capsys):
        with patch("sys.argv", ["crawl.py", "https://docs.stripe.com/payments", "--show-info"]):
            main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["tool_name"] == "stripe"
        assert data["scope_host"] == "docs.stripe.com"
        assert data["scope_prefix"] == "/payments"

    def test_root_url_scope_prefix(self, capsys):
        with patch("sys.argv", ["crawl.py", "https://docs.stripe.com/", "--show-info"]):
            main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["scope_prefix"] == "/"

    def test_tool_name_override_reflected(self, capsys):
        with patch("sys.argv", ["crawl.py", "https://docs.stripe.com/payments", "--show-info", "--tool-name", "my-stripe"]):
            main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["tool_name"] == "my-stripe"

    def test_does_not_crawl_on_show_info(self, capsys):
        with patch("sys.argv", ["crawl.py", "https://docs.stripe.com/payments", "--show-info"]), \
             patch("crawl.crawl_site") as mock_site, \
             patch("crawl.crawl_page") as mock_page:
            main()
        mock_site.assert_not_called()
        mock_page.assert_not_called()


class TestSkillFile:
    @pytest.fixture
    def skill_content(self):
        skill_path = Path(__file__).parent.parent / "SKILL.md"
        assert skill_path.exists(), "SKILL.md must exist in the repo root"
        return skill_path.read_text(encoding="utf-8")

    def test_skill_md_has_description_frontmatter(self, skill_content):
        assert skill_content.startswith("---"), "SKILL.md must start with YAML frontmatter"
        assert "description:" in skill_content

    def test_skill_md_references_uv_run(self, skill_content):
        assert "uv run" in skill_content, "SKILL.md must orchestrate crawler via uv run"

    def test_skill_md_has_confirmation_step(self, skill_content):
        assert "confirm" in skill_content.lower()

    def test_skill_md_references_show_info(self, skill_content):
        assert "--show-info" in skill_content


# ---------------------------------------------------------------------------
# Issue 9: Summarization fan-out
# ---------------------------------------------------------------------------


def _make_summarizable_page(tmp_path, tool_name, page_path, content_hash="abc", summary=None):
    md_path = tmp_path / "docs" / "tools" / tool_name / (page_path + ".md")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    fm = {"title": "Test", "content_hash": content_hash}
    if summary:
        fm["summary"] = summary
    fm_text = "---\n" + yaml.dump(fm) + "---\n\n# Test\n\nContent.\n"
    md_path.write_text(fm_text, encoding="utf-8")
    return md_path


class TestBatchPagesByDirectory:
    def test_groups_urls_in_same_dir_together(self):
        urls = [
            "https://docs.stripe.com/payments/charges",
            "https://docs.stripe.com/payments/refunds",
            "https://docs.stripe.com/api/intro",
        ]
        batches = batch_pages_by_directory(urls, batch_size=15)
        flat = [url for batch in batches for url in batch]
        assert sorted(flat) == sorted(urls)
        # Two directories → two batches
        assert len(batches) == 2

    def test_splits_large_directory(self):
        urls = [f"https://docs.stripe.com/api/page{i}" for i in range(20)]
        batches = batch_pages_by_directory(urls, batch_size=15)
        assert len(batches) == 2
        assert all(len(b) <= 15 for b in batches)

    def test_cap_respected(self):
        urls = [f"https://docs.stripe.com/api/page{i}" for i in range(30)]
        batches = batch_pages_by_directory(urls, batch_size=10)
        assert all(len(b) <= 10 for b in batches)

    def test_all_urls_covered(self):
        urls = [f"https://docs.stripe.com/dir{i}/page" for i in range(5)]
        batches = batch_pages_by_directory(urls, batch_size=15)
        flat = [url for batch in batches for url in batch]
        assert sorted(flat) == sorted(urls)

    def test_root_level_pages(self):
        urls = ["https://docs.stripe.com/", "https://docs.stripe.com/index"]
        batches = batch_pages_by_directory(urls, batch_size=15)
        flat = [url for batch in batches for url in batch]
        assert sorted(flat) == sorted(urls)

    def test_single_url(self):
        urls = ["https://docs.stripe.com/payments/charges"]
        batches = batch_pages_by_directory(urls, batch_size=15)
        assert len(batches) == 1
        assert batches[0] == urls

    def test_empty_urls(self):
        assert batch_pages_by_directory([], batch_size=15) == []


class TestReadPageFrontmatter:
    def test_reads_yaml_frontmatter(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: Test\nsource_url: https://x.com\n---\n\nBody\n", encoding="utf-8")
        fm = read_page_frontmatter(md)
        assert fm["title"] == "Test"
        assert fm["source_url"] == "https://x.com"

    def test_returns_empty_dict_when_no_frontmatter(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("# Heading\n\nContent\n", encoding="utf-8")
        assert read_page_frontmatter(md) == {}

    def test_reads_all_fields(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: T\ncontent_hash: abc\n---\n\nBody\n", encoding="utf-8")
        fm = read_page_frontmatter(md)
        assert fm["content_hash"] == "abc"

    def test_returns_empty_when_unclosed(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: T\n# Body without closing delimiter\n", encoding="utf-8")
        assert read_page_frontmatter(md) == {}


class TestWritePageFrontmatter:
    def test_adds_summary_to_existing_frontmatter(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: Test\n---\n\nBody content\n", encoding="utf-8")
        write_page_frontmatter(md, {"summary": "A terse summary", "keywords": ["API"]})
        fm = read_page_frontmatter(md)
        assert fm["summary"] == "A terse summary"
        assert fm["keywords"] == ["API"]

    def test_preserves_body_content(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: Test\n---\n\nBody content here.\n", encoding="utf-8")
        write_page_frontmatter(md, {"summary": "Summary"})
        assert "Body content here." in md.read_text()

    def test_preserves_existing_frontmatter_fields(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: Test\ncontent_hash: abc\n---\n\nBody\n", encoding="utf-8")
        write_page_frontmatter(md, {"summary": "Summary"})
        fm = read_page_frontmatter(md)
        assert fm["title"] == "Test"
        assert fm["content_hash"] == "abc"

    def test_updates_existing_summary(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: T\nsummary: old\n---\n\nBody\n", encoding="utf-8")
        write_page_frontmatter(md, {"summary": "new summary"})
        assert read_page_frontmatter(md)["summary"] == "new summary"

    def test_file_still_valid_frontmatter_after_update(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: Test\n---\n\nBody\n", encoding="utf-8")
        write_page_frontmatter(md, {"summary": "S", "keywords": ["k1", "k2"]})
        text = md.read_text()
        assert text.startswith("---\n")
        assert text.count("---\n") >= 2


class TestIsPageSummarized:
    def test_true_when_summary_present(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\nsummary: A summary.\n---\n\nBody\n", encoding="utf-8")
        assert is_page_summarized(md) is True

    def test_false_when_no_summary(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\ntitle: Test\n---\n\nBody\n", encoding="utf-8")
        assert is_page_summarized(md) is False

    def test_false_when_summary_empty_string(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("---\nsummary: ''\n---\n\nBody\n", encoding="utf-8")
        assert is_page_summarized(md) is False

    def test_false_when_no_frontmatter(self, tmp_path):
        md = tmp_path / "page.md"
        md.write_text("# Just a heading\n\nContent\n", encoding="utf-8")
        assert is_page_summarized(md) is False


class TestSummarizeBatch:
    def test_calls_llm_for_unsummarized_page(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges", content_hash="abc")
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "content_hash": "abc",
            }
        }
        with patch("crawl.call_summarize_llm", return_value={"summary": "A charge page.", "keywords": ["Charge"], "candidates": ["Charge object"]}) as mock_llm:
            result = _summarize_batch(["https://docs.stripe.com/payments/charges"], manifest, "stripe", tmp_path)
        mock_llm.assert_called_once()
        assert result["done"] == 1
        assert result["failed"] == 0

    def test_skips_already_summarized_unchanged_page(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges", content_hash="abc", summary="Already done.")
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "content_hash": "abc",
            }
        }
        with patch("crawl.call_summarize_llm") as mock_llm:
            result = _summarize_batch(["https://docs.stripe.com/payments/charges"], manifest, "stripe", tmp_path)
        mock_llm.assert_not_called()
        assert result["done"] == 0
        assert result["failed"] == 0

    def test_resumes_if_content_hash_changed(self, tmp_path):
        # Frontmatter has old hash + summary, manifest has new hash (page was recrawled)
        _make_summarizable_page(tmp_path, "stripe", "payments/charges", content_hash="old_hash", summary="Old summary.")
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "content_hash": "new_hash",
            }
        }
        with patch("crawl.call_summarize_llm", return_value={"summary": "New.", "keywords": [], "candidates": []}) as mock_llm:
            result = _summarize_batch(["https://docs.stripe.com/payments/charges"], manifest, "stripe", tmp_path)
        mock_llm.assert_called_once()
        assert result["done"] == 1

    def test_writes_summary_and_keywords_to_frontmatter(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges", content_hash="abc")
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "content_hash": "abc",
            }
        }
        with patch("crawl.call_summarize_llm", return_value={"summary": "Charge API.", "keywords": ["Charge"], "candidates": []}):
            _summarize_batch(["https://docs.stripe.com/payments/charges"], manifest, "stripe", tmp_path)
        md = tmp_path / "docs/tools/stripe/payments/charges.md"
        fm = read_page_frontmatter(md)
        assert fm["summary"] == "Charge API."
        assert fm["keywords"] == ["Charge"]

    def test_returns_candidate_terms(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges", content_hash="abc")
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "content_hash": "abc",
            }
        }
        with patch("crawl.call_summarize_llm", return_value={"summary": "T.", "keywords": [], "candidates": ["Charge object", "PaymentIntent"]}):
            result = _summarize_batch(["https://docs.stripe.com/payments/charges"], manifest, "stripe", tmp_path)
        assert "Charge object" in result["candidates"]
        assert "PaymentIntent" in result["candidates"]

    def test_increments_failed_on_missing_markdown_file(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "content_hash": "abc",
            }
        }
        result = _summarize_batch(["https://docs.stripe.com/payments/charges"], manifest, "stripe", tmp_path)
        assert result["failed"] == 1
        assert result["done"] == 0

    def test_increments_failed_on_llm_error(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges", content_hash="abc")
        manifest = {
            "https://docs.stripe.com/payments/charges": {
                "page_path": "payments/charges",
                "content_hash": "abc",
            }
        }
        with patch("crawl.call_summarize_llm", side_effect=Exception("API error")):
            result = _summarize_batch(["https://docs.stripe.com/payments/charges"], manifest, "stripe", tmp_path)
        assert result["failed"] == 1
        assert result["done"] == 0

    def test_increments_failed_when_url_not_in_manifest(self, tmp_path):
        result = _summarize_batch(["https://docs.stripe.com/missing"], {}, "stripe", tmp_path)
        assert result["failed"] == 1

    def test_batch_with_multiple_pages(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges", content_hash="abc")
        _make_summarizable_page(tmp_path, "stripe", "payments/refunds", content_hash="abc")
        manifest = {
            "https://docs.stripe.com/payments/charges": {"page_path": "payments/charges", "content_hash": "abc"},
            "https://docs.stripe.com/payments/refunds": {"page_path": "payments/refunds", "content_hash": "abc"},
        }
        with patch("crawl.call_summarize_llm", return_value={"summary": "T.", "keywords": [], "candidates": ["term"]}):
            result = _summarize_batch(
                ["https://docs.stripe.com/payments/charges", "https://docs.stripe.com/payments/refunds"],
                manifest, "stripe", tmp_path,
            )
        assert result["done"] == 2
        assert result["failed"] == 0
        assert result["candidates"].count("term") == 2


class TestSummarizeSite:
    def test_processes_all_unsummarized_pages(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges")
        _make_summarizable_page(tmp_path, "stripe", "payments/refunds")
        manifest = {
            "https://docs.stripe.com/payments/charges": {"page_path": "payments/charges", "content_hash": "abc"},
            "https://docs.stripe.com/payments/refunds": {"page_path": "payments/refunds", "content_hash": "abc"},
        }
        save_manifest(manifest, "stripe", tmp_path)
        with patch("crawl.call_summarize_llm", return_value={"summary": "T.", "keywords": [], "candidates": ["term"]}):
            result = summarize_site("stripe", tmp_path)
        assert result["done"] == 2
        assert result["failed"] == 0

    def test_consolidates_candidates_from_all_batches(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges")
        _make_summarizable_page(tmp_path, "stripe", "api/intro")
        manifest = {
            "https://docs.stripe.com/payments/charges": {"page_path": "payments/charges", "content_hash": "abc"},
            "https://docs.stripe.com/api/intro": {"page_path": "api/intro", "content_hash": "abc"},
        }
        save_manifest(manifest, "stripe", tmp_path)

        def fake_llm(content, url):
            if "charges" in url:
                return {"summary": "T.", "keywords": [], "candidates": ["Charge"]}
            return {"summary": "T.", "keywords": [], "candidates": ["PaymentIntent"]}

        with patch("crawl.call_summarize_llm", side_effect=fake_llm):
            result = summarize_site("stripe", tmp_path)
        assert "Charge" in result["candidates"]
        assert "PaymentIntent" in result["candidates"]

    def test_concurrency_ceiling_enforced(self, tmp_path):
        for i in range(6):
            _make_summarizable_page(tmp_path, "stripe", f"dir{i}/page")
        manifest = {
            f"https://docs.stripe.com/dir{i}/page": {
                "page_path": f"dir{i}/page",
                "content_hash": "abc",
            }
            for i in range(6)
        }
        save_manifest(manifest, "stripe", tmp_path)

        counter = {"current": 0, "peak": 0}
        lock = threading.Lock()

        def slow_batch(urls, manifest, tool_name, base_dir):
            with lock:
                counter["current"] += 1
                counter["peak"] = max(counter["peak"], counter["current"])
            time.sleep(0.02)
            with lock:
                counter["current"] -= 1
            return {"candidates": [], "done": 1, "failed": 0}

        with patch("crawl._summarize_batch", side_effect=slow_batch):
            summarize_site("stripe", tmp_path, concurrency=3)

        assert counter["peak"] <= 3

    def test_returns_aggregated_done_failed_totals(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges")
        manifest = {
            "https://docs.stripe.com/payments/charges": {"page_path": "payments/charges", "content_hash": "abc"},
            # This URL has no markdown file → will fail
            "https://docs.stripe.com/payments/missing": {"page_path": "payments/missing", "content_hash": "abc"},
        }
        save_manifest(manifest, "stripe", tmp_path)
        with patch("crawl.call_summarize_llm", return_value={"summary": "T.", "keywords": [], "candidates": []}):
            result = summarize_site("stripe", tmp_path)
        assert result["done"] == 1
        assert result["failed"] == 1

    def test_loads_manifest_from_disk(self, tmp_path):
        _make_summarizable_page(tmp_path, "stripe", "payments/charges")
        manifest = {
            "https://docs.stripe.com/payments/charges": {"page_path": "payments/charges", "content_hash": "abc"},
        }
        save_manifest(manifest, "stripe", tmp_path)
        with patch("crawl.call_summarize_llm", return_value={"summary": "T.", "keywords": [], "candidates": []}):
            result = summarize_site("stripe", tmp_path)
        assert result["done"] == 1

    def test_empty_manifest_returns_zero_counts(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        result = summarize_site("stripe", tmp_path)
        assert result == {"candidates": [], "done": 0, "failed": 0}


# ---------------------------------------------------------------------------
# Issue 10: Synthesis — Glossary, Page map, Tools map
# ---------------------------------------------------------------------------


class TestCallGlossaryLlm:
    def test_returns_empty_list_for_empty_terms(self):
        result = call_glossary_llm([], "stripe")
        assert result == []

    def test_calls_anthropic_and_parses_response(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='[{"term": "Charge", "definition": "A payment object."}]')]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = call_glossary_llm(["charge"], "stripe")
        assert result == [{"term": "Charge", "definition": "A payment object."}]

    def test_strips_code_fences_from_response(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='```json\n[{"term": "Refund", "definition": "A return."}]\n```')]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = call_glossary_llm(["refund"], "stripe")
        assert result == [{"term": "Refund", "definition": "A return."}]

    def test_filters_entries_missing_term_or_definition(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='[{"term": "Good", "definition": "OK"}, {"term": "", "definition": "Bad"}, {"term": "Also bad"}]')]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = call_glossary_llm(["good", "bad"], "stripe")
        assert len(result) == 1
        assert result[0]["term"] == "Good"


class TestGenerateGlossary:
    def test_writes_to_correct_path(self, tmp_path):
        with patch("crawl.call_glossary_llm", return_value=[]):
            path = generate_glossary("stripe", ["charge"], tmp_path)
        assert path == tmp_path / "docs" / "tools" / "stripe" / "CONTEXT.md"
        assert path.exists()

    def test_contains_auto_generated_marker(self, tmp_path):
        with patch("crawl.call_glossary_llm", return_value=[]):
            path = generate_glossary("stripe", [], tmp_path)
        assert "<!-- auto-generated -->" in path.read_text()

    def test_deduplicates_candidates_case_insensitive(self, tmp_path):
        calls = []
        def capture(terms, tool_name):
            calls.append(terms[:])
            return []
        with patch("crawl.call_glossary_llm", side_effect=capture):
            generate_glossary("stripe", ["Charge", "charge", "CHARGE", "PaymentIntent"], tmp_path)
        assert len(calls) == 1
        unique = calls[0]
        assert unique.count("Charge") == 1 or unique.count("charge") + unique.count("CHARGE") == 0
        assert len(unique) == 2

    def test_writes_term_definitions_to_file(self, tmp_path):
        entries = [
            {"term": "Charge", "definition": "A payment object representing a debit."},
            {"term": "Refund", "definition": "A reversal of a Charge."},
        ]
        with patch("crawl.call_glossary_llm", return_value=entries):
            path = generate_glossary("stripe", ["charge", "refund"], tmp_path)
        content = path.read_text()
        assert "**Charge**" in content
        assert "A payment object representing a debit." in content
        assert "**Refund**" in content

    def test_empty_candidates_writes_header_only(self, tmp_path):
        with patch("crawl.call_glossary_llm", return_value=[]):
            path = generate_glossary("stripe", [], tmp_path)
        content = path.read_text()
        assert "stripe Glossary" in content
        assert "<!-- auto-generated -->" in content

    def test_creates_parent_directories(self, tmp_path):
        with patch("crawl.call_glossary_llm", return_value=[]):
            path = generate_glossary("new-tool", [], tmp_path)
        assert path.exists()

    def test_does_not_call_llm_for_empty_candidates(self, tmp_path):
        with patch("crawl.call_glossary_llm") as mock_llm:
            mock_llm.return_value = []
            generate_glossary("stripe", [], tmp_path)
        mock_llm.assert_called_once_with([], "stripe")


class TestGeneratePageMap:
    def _make_page(self, tmp_path, tool_name, page_path, summary=None):
        md_path = tmp_path / "docs" / "tools" / tool_name / (page_path + ".md")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        fm = {"title": "Test", "content_hash": "abc"}
        if summary:
            fm["summary"] = summary
        fm_text = "---\n" + yaml.dump(fm) + "---\n\n# Test\n"
        md_path.write_text(fm_text, encoding="utf-8")
        return md_path

    def test_writes_to_correct_path(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/payments": {"page_path": "payments"},
        }
        self._make_page(tmp_path, "stripe", "payments")
        save_manifest(manifest, "stripe", tmp_path)
        path = generate_page_map("stripe", tmp_path)
        assert path == tmp_path / "docs" / "tools" / "stripe" / "_index.md"
        assert path.exists()

    def test_contains_auto_generated_marker(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        path = generate_page_map("stripe", tmp_path)
        assert "<!-- auto-generated -->" in path.read_text()

    def test_lists_pages_with_summaries(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/payments": {"page_path": "payments"},
        }
        self._make_page(tmp_path, "stripe", "payments", summary="Payments overview.")
        save_manifest(manifest, "stripe", tmp_path)
        path = generate_page_map("stripe", tmp_path)
        content = path.read_text()
        assert "Payments overview." in content
        assert "payments.md" in content

    def test_lists_page_without_summary(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/payments": {"page_path": "payments"},
        }
        self._make_page(tmp_path, "stripe", "payments")
        save_manifest(manifest, "stripe", tmp_path)
        path = generate_page_map("stripe", tmp_path)
        content = path.read_text()
        assert "payments.md" in content

    def test_entries_sorted_by_url_path(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/refunds": {"page_path": "refunds"},
            "https://docs.stripe.com/api": {"page_path": "api"},
            "https://docs.stripe.com/payments": {"page_path": "payments"},
        }
        for page_path in ["refunds", "api", "payments"]:
            self._make_page(tmp_path, "stripe", page_path)
        save_manifest(manifest, "stripe", tmp_path)
        path = generate_page_map("stripe", tmp_path)
        content = path.read_text()
        api_pos = content.index("api.md")
        payments_pos = content.index("payments.md")
        refunds_pos = content.index("refunds.md")
        assert api_pos < payments_pos < refunds_pos

    def test_accepts_manifest_argument(self, tmp_path):
        manifest = {
            "https://docs.stripe.com/charges": {"page_path": "charges"},
        }
        self._make_page(tmp_path, "stripe", "charges", summary="Charge API.")
        path = generate_page_map("stripe", tmp_path, manifest=manifest)
        assert "charges.md" in path.read_text()

    def test_empty_manifest_writes_header_only(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        path = generate_page_map("stripe", tmp_path)
        content = path.read_text()
        assert "stripe — Page Map" in content
        assert "<!-- auto-generated -->" in content


class TestUpdateToolsMap:
    def test_creates_file_if_absent(self, tmp_path):
        path = update_tools_map("stripe", "https://docs.stripe.com/", tmp_path)
        assert path == tmp_path / "docs" / "tools" / "CONTEXT-MAP.md"
        assert path.exists()

    def test_adds_tool_entry(self, tmp_path):
        path = update_tools_map("stripe", "https://docs.stripe.com/", tmp_path)
        content = path.read_text()
        assert "stripe" in content

    def test_idempotent_on_second_call(self, tmp_path):
        update_tools_map("stripe", "https://docs.stripe.com/", tmp_path)
        path = update_tools_map("stripe", "https://docs.stripe.com/", tmp_path)
        content = path.read_text()
        assert content.count("stripe") == content.count("stripe")
        assert content.count("- **stripe**") == 1

    def test_multiple_tools_do_not_duplicate(self, tmp_path):
        update_tools_map("stripe", "https://docs.stripe.com/", tmp_path)
        update_tools_map("react", "https://react.dev/", tmp_path)
        update_tools_map("stripe", "https://docs.stripe.com/", tmp_path)
        content = (tmp_path / "docs" / "tools" / "CONTEXT-MAP.md").read_text()
        assert content.count("- **stripe**") == 1
        assert content.count("- **react**") == 1

    def test_created_file_has_header(self, tmp_path):
        path = update_tools_map("stripe", "https://docs.stripe.com/", tmp_path)
        content = path.read_text()
        assert "# Tools Map" in content

    def test_links_to_tool_directory(self, tmp_path):
        path = update_tools_map("stripe", "https://docs.stripe.com/", tmp_path)
        content = path.read_text()
        assert "stripe/" in content


class TestSynthesisSite:
    def test_returns_all_three_paths(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        with patch("crawl.call_glossary_llm", return_value=[]):
            result = synthesize_site("stripe", [], "https://docs.stripe.com/", tmp_path)
        assert "glossary_path" in result
        assert "page_map_path" in result
        assert "tools_map_path" in result

    def test_glossary_path_correct(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        with patch("crawl.call_glossary_llm", return_value=[]):
            result = synthesize_site("stripe", [], "https://docs.stripe.com/", tmp_path)
        assert result["glossary_path"] == tmp_path / "docs" / "tools" / "stripe" / "CONTEXT.md"

    def test_page_map_path_correct(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        with patch("crawl.call_glossary_llm", return_value=[]):
            result = synthesize_site("stripe", [], "https://docs.stripe.com/", tmp_path)
        assert result["page_map_path"] == tmp_path / "docs" / "tools" / "stripe" / "_index.md"

    def test_tools_map_path_correct(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        with patch("crawl.call_glossary_llm", return_value=[]):
            result = synthesize_site("stripe", [], "https://docs.stripe.com/", tmp_path)
        assert result["tools_map_path"] == tmp_path / "docs" / "tools" / "CONTEXT-MAP.md"

    def test_passes_candidates_to_glossary(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        calls = []
        def capture(terms, tool_name):
            calls.append(terms[:])
            return []
        with patch("crawl.call_glossary_llm", side_effect=capture):
            synthesize_site("stripe", ["Charge", "Refund"], "https://docs.stripe.com/", tmp_path)
        assert len(calls) == 1
        assert "Charge" in calls[0]
        assert "Refund" in calls[0]

    def test_all_three_files_written(self, tmp_path):
        save_manifest({}, "stripe", tmp_path)
        with patch("crawl.call_glossary_llm", return_value=[]):
            result = synthesize_site("stripe", [], "https://docs.stripe.com/", tmp_path)
        assert result["glossary_path"].exists()
        assert result["page_map_path"].exists()
        assert result["tools_map_path"].exists()


# ---------------------------------------------------------------------------
# Issue 4: Code-block fidelity
# ---------------------------------------------------------------------------


class TestPreprocessCodeBlocks:
    """Unit tests for preprocess_code_blocks — operates on a BeautifulSoup directly."""

    def _soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    # AC1 – no span artifacts, whitespace preserved

    def test_strips_syntax_highlight_spans(self):
        soup = self._soup(
            '<pre><code class="language-python">'
            '<span class="kw">def</span> <span class="fn">foo</span>():\n    pass'
            "</code></pre>"
        )
        preprocess_code_blocks(soup)
        result = str(soup)
        assert "<span" not in result
        assert "def foo():" in result
        assert "    pass" in result

    def test_whitespace_preserved(self):
        soup = self._soup(
            '<pre><code class="language-python">def foo():\n\tbar = 1\n\treturn bar</code></pre>'
        )
        preprocess_code_blocks(soup)
        code = soup.find("code")
        text = code.get_text()
        assert "\tbar = 1" in text
        assert "\treturn bar" in text

    # AC2 – language tag detection

    def test_detects_language_from_language_prefix(self):
        soup = self._soup('<pre><code class="language-python">x = 1</code></pre>')
        preprocess_code_blocks(soup)
        code = soup.find("code")
        assert "language-python" in (code.get("class") or [])

    def test_detects_language_from_lang_prefix(self):
        soup = self._soup('<pre><code class="lang-javascript">const x = 1;</code></pre>')
        preprocess_code_blocks(soup)
        code = soup.find("code")
        assert "language-javascript" in (code.get("class") or [])

    def test_detects_language_from_data_lang(self):
        soup = self._soup('<pre data-lang="bash"><code>ls -la</code></pre>')
        preprocess_code_blocks(soup)
        code = soup.find("code")
        assert "language-bash" in (code.get("class") or [])

    def test_no_class_when_no_language(self):
        soup = self._soup("<pre><code>some code</code></pre>")
        preprocess_code_blocks(soup)
        code = soup.find("code")
        assert not code.get("class")

    # AC3 – line-number gutters and copy buttons stripped

    def test_strips_line_numbers_rows(self):
        soup = self._soup(
            '<pre><code class="language-python">x = 1</code>'
            '<span class="line-numbers-rows"><span>1</span></span></pre>'
        )
        preprocess_code_blocks(soup)
        result = str(soup)
        assert "line-numbers-rows" not in result
        assert "x = 1" in result

    def test_strips_gutter_element(self):
        soup = self._soup(
            '<pre><div class="gutter"><span>1</span><span>2</span></div>'
            '<code class="language-python">x = 1\ny = 2</code></pre>'
        )
        preprocess_code_blocks(soup)
        result = str(soup)
        assert 'class="gutter"' not in result
        assert "x = 1" in result

    def test_strips_copy_button(self):
        soup = self._soup(
            '<pre><button class="copy-code">Copy</button>'
            '<code class="language-python">x = 1</code></pre>'
        )
        preprocess_code_blocks(soup)
        result = str(soup)
        assert "<button" not in result
        assert "Copy" not in result
        assert "x = 1" in result

    # AC4 – tabbed code blocks → sequential labeled fences

    def test_tabbed_set_expands_all_variants(self):
        soup = self._soup("""<div class="tabbed-set">
            <div class="tabbed-labels">
                <label>Python</label>
                <label>JavaScript</label>
            </div>
            <div class="tabbed-content">
                <div class="tabbed-block">
                    <pre><code class="language-python">x = 1</code></pre>
                </div>
                <div class="tabbed-block">
                    <pre><code class="language-javascript">const x = 1;</code></pre>
                </div>
            </div>
        </div>""")
        preprocess_code_blocks(soup)
        result = str(soup)
        assert "tabbed-set" not in result
        assert "Python" in result
        assert "JavaScript" in result
        assert "x = 1" in result
        assert "const x = 1;" in result

    def test_aria_tabs_expand_all_variants(self):
        soup = self._soup("""<div>
            <div role="tablist">
                <button role="tab">Python</button>
                <button role="tab">JS</button>
            </div>
            <div role="tabpanel">
                <pre><code class="language-python">x = 1</code></pre>
            </div>
            <div role="tabpanel">
                <pre><code class="language-javascript">const x = 1;</code></pre>
            </div>
        </div>""")
        preprocess_code_blocks(soup)
        result = str(soup)
        assert 'role="tablist"' not in result
        assert "Python" in result
        assert "JS" in result
        assert "x = 1" in result
        assert "const x = 1;" in result


class TestCodeBlockFidelity:
    """Integration tests: extract_content + to_markdown pipeline (Issue 4)."""

    def test_no_span_artifacts_in_output(self):
        html = """<html><body><main>
            <pre><code class="language-python"><span class="kw">def</span> <span>foo</span>():
    pass</code></pre>
        </main></body></html>"""
        _, content = extract_content(html)
        md = to_markdown(content)
        assert "<span" not in md
        assert "def foo():" in md

    def test_language_tag_applied_to_fence(self):
        html = """<html><body><main>
            <pre><code class="language-python">x = 1</code></pre>
        </main></body></html>"""
        _, content = extract_content(html)
        md = to_markdown(content)
        assert "```python" in md

    def test_lang_prefix_class_produces_fence_tag(self):
        html = """<html><body><main>
            <pre><code class="lang-typescript">const x: number = 1;</code></pre>
        </main></body></html>"""
        _, content = extract_content(html)
        md = to_markdown(content)
        assert "```typescript" in md

    def test_bare_fence_when_no_language(self):
        html = """<html><body><main>
            <pre><code>some code here</code></pre>
        </main></body></html>"""
        _, content = extract_content(html)
        md = to_markdown(content)
        assert "```" in md
        assert "some code here" in md

    def test_line_number_gutters_not_in_code(self):
        html = """<html><body><main>
            <pre><code class="language-python">x = 1\ny = 2</code>\
<span class="line-numbers-rows"><span>1</span><span>2</span></span></pre>
        </main></body></html>"""
        _, content = extract_content(html)
        md = to_markdown(content)
        assert "x = 1" in md
        assert "y = 2" in md
        # gutter digits must not appear on their own line inside the fence
        fence_content = md[md.find("```") : md.rfind("```") + 3]
        lines = [ln.strip() for ln in fence_content.splitlines() if ln.strip() and not ln.startswith("```")]
        assert all(ln not in ("1", "2") for ln in lines)

    def test_copy_button_not_in_code(self):
        html = """<html><body><main>
            <pre>
                <button class="copy-code">Copy</button>
                <code class="language-python">x = 1</code>
            </pre>
        </main></body></html>"""
        _, content = extract_content(html)
        md = to_markdown(content)
        assert "x = 1" in md
        fence_content = md[md.find("```") : md.rfind("```") + 3]
        assert "Copy" not in fence_content

    def test_whitespace_preserved(self):
        html = """<html><body><main>
            <pre><code class="language-python">def foo():\n\tbar = 1\n\treturn bar</code></pre>
        </main></body></html>"""
        _, content = extract_content(html)
        md = to_markdown(content)
        assert "\tbar = 1" in md

    def test_tabbed_blocks_emit_all_variants(self):
        html = """<html><body><main>
            <div class="tabbed-set">
                <div class="tabbed-labels">
                    <label>Python</label>
                    <label>JavaScript</label>
                </div>
                <div class="tabbed-content">
                    <div class="tabbed-block">
                        <pre><code class="language-python">x = 1</code></pre>
                    </div>
                    <div class="tabbed-block">
                        <pre><code class="language-javascript">const x = 1;</code></pre>
                    </div>
                </div>
            </div>
        </main></body></html>"""
        _, content = extract_content(html)
        md = to_markdown(content)
        assert "Python" in md
        assert "JavaScript" in md
        assert "x = 1" in md
        assert "const x = 1;" in md
        assert md.count("```") >= 4
