"""Document fingerprint utilities."""

from hashlib import sha256


def sha256_bytes(document: bytes) -> str:
    """Return the lowercase SHA-256 digest of the exact document bytes."""

    return sha256(document).hexdigest()
