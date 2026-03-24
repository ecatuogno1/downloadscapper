"""Tests for website_download_summary utility functions."""

from __future__ import annotations

import pytest

from website_download_summary import (
    DOWNLOAD_EXTENSIONS,
    crawl_scope_prefix,
    decode_base64_text,
    extension_from_value,
    file_extension,
    host_matches,
    looks_like_download_url,
    normalize_space,
    normalize_url,
    page_is_in_scope,
    parse_human_size_to_bytes,
    text_contains_download_hint,
)


class TestNormalizeUrl:
    def test_absolute_url_unchanged(self):
        result = normalize_url("https://example.com/page", "https://example.com/file.zip")
        assert result == "https://example.com/file.zip"

    def test_relative_url_resolved(self):
        result = normalize_url("https://example.com/dir/page.html", "../files/data.zip")
        assert result == "https://example.com/files/data.zip"

    def test_fragment_stripped(self):
        result = normalize_url("https://example.com/", "https://example.com/page#section")
        assert "#section" not in (result or "")

    def test_javascript_scheme_rejected(self):
        result = normalize_url("https://example.com/", "javascript:void(0)")
        assert result is None

    def test_mailto_rejected(self):
        result = normalize_url("https://example.com/", "mailto:x@example.com")
        assert result is None


class TestHostMatches:
    def test_exact_match(self):
        assert host_matches("example.com", "example.com", allow_subdomains=False)

    def test_subdomain_rejected_without_flag(self):
        assert not host_matches("sub.example.com", "example.com", allow_subdomains=False)

    def test_subdomain_accepted_with_flag(self):
        assert host_matches("sub.example.com", "example.com", allow_subdomains=True)

    def test_different_domain_rejected(self):
        assert not host_matches("other.com", "example.com", allow_subdomains=True)


class TestLooksLikeDownloadUrl:
    def test_known_extension(self):
        result, reason = looks_like_download_url("https://example.com/file.zip")
        assert result is True
        assert "extension" in reason

    def test_html_page_rejected(self):
        result, _ = looks_like_download_url("https://example.com/page.html")
        assert result is False

    def test_download_attr(self):
        result, reason = looks_like_download_url(
            "https://example.com/get", has_download_attr=True
        )
        assert result is True

    def test_download_hint_in_path(self):
        result, _ = looks_like_download_url("https://example.com/downloads/file")
        assert result is True

    def test_anchor_text_hint(self):
        result, _ = looks_like_download_url(
            "https://example.com/get", anchor_text="Download PDF"
        )
        assert result is True

    def test_iso_extension(self):
        result, _ = looks_like_download_url("https://example.com/game.iso")
        assert result is True


class TestParseHumanSizeToBytes:
    def test_bytes(self):
        assert parse_human_size_to_bytes("500 B") == 500

    def test_kilobytes(self):
        assert parse_human_size_to_bytes("1 KB") == 1024

    def test_megabytes(self):
        assert parse_human_size_to_bytes("1 MB") == 1024 ** 2

    def test_gigabytes(self):
        assert parse_human_size_to_bytes("2 GB") == 2 * 1024 ** 3

    def test_decimal(self):
        result = parse_human_size_to_bytes("1.5 MB")
        assert result == int(1.5 * 1024 ** 2)

    def test_invalid_returns_none(self):
        assert parse_human_size_to_bytes("not a size") is None


class TestNormalizeSpace:
    def test_collapses_whitespace(self):
        assert normalize_space("  hello   world  ") == "hello world"

    def test_tabs_and_newlines(self):
        assert normalize_space("a\tb\nc") == "a b c"

    def test_empty_string(self):
        assert normalize_space("") == ""


class TestDecodeBase64Text:
    def test_valid_base64(self):
        import base64
        encoded = base64.b64encode(b"hello world").decode()
        assert decode_base64_text(encoded) == "hello world"

    def test_invalid_returns_none(self):
        assert decode_base64_text("not-base64!!!") is None

    def test_empty_after_decode_returns_none(self):
        import base64
        encoded = base64.b64encode(b"   ").decode()
        assert decode_base64_text(encoded) is None


class TestTextContainsDownloadHint:
    def test_detects_download_word(self):
        assert text_contains_download_hint("Click here to download")

    def test_detects_extension(self):
        assert text_contains_download_hint("Get the .zip file here")

    def test_no_hint(self):
        assert not text_contains_download_hint("This is a blog post about cats")

    def test_empty(self):
        assert not text_contains_download_hint("")


class TestCrawlScopePrefix:
    def test_root_url_no_prefix(self):
        assert crawl_scope_prefix("https://example.com/") is None

    def test_path_with_id_stripped(self):
        result = crawl_scope_prefix("https://example.com/vault/123")
        assert result == "/vault"

    def test_section_prefix_preserved(self):
        result = crawl_scope_prefix("https://example.com/games/ps2")
        assert result == "/games/ps2"

    def test_file_extension_stripped(self):
        result = crawl_scope_prefix("https://example.com/catalog/index.html")
        assert result == "/catalog"


class TestPageIsInScope:
    def test_no_scope_prefix_always_in_scope(self):
        from website_download_summary import page_is_in_scope
        assert page_is_in_scope("https://example.com/anything", None)

    def test_matching_prefix(self):
        from website_download_summary import page_is_in_scope
        assert page_is_in_scope("https://example.com/vault/ps2/games", "/vault/ps2")

    def test_non_matching_prefix(self):
        from website_download_summary import page_is_in_scope
        assert not page_is_in_scope("https://example.com/other/page", "/vault")
