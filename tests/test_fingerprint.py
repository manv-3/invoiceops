"""Tests for document fingerprinting."""

from app.utils.fingerprint import compute_fingerprint


def test_same_content_same_fingerprint() -> None:
    content = b"hello invoice"
    assert compute_fingerprint(content) == compute_fingerprint(content)


def test_different_content_different_fingerprint() -> None:
    assert compute_fingerprint(b"a") != compute_fingerprint(b"b")


def test_returns_hex_string() -> None:
    fp = compute_fingerprint(b"test")
    assert len(fp) == 64  # SHA-256 hex digest
    assert all(c in "0123456789abcdef" for c in fp)
