"""SHA-256 document fingerprinting."""

import hashlib


def compute_fingerprint(content: bytes) -> str:
    """Return the hex-encoded SHA-256 digest of *content*."""
    return hashlib.sha256(content).hexdigest()
