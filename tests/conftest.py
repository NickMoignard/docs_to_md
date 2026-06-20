"""Shared pytest fixtures for crawl tests."""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stub_playwright(request):
    """Stub Playwright functions for all tests unless marked @pytest.mark.playwright."""
    if request.node.get_closest_marker("playwright"):
        yield
        return
    with patch("crawl.ensure_playwright_chromium"), \
         patch("crawl.fetch_with_playwright", return_value="<html><body></body></html>"):
        yield
