from app.utils.hashing import sha256_bytes


def test_sha256_is_deterministic_for_document_bytes() -> None:
    assert sha256_bytes(b"invoice-bytes") == (
        "4a64bec647eeafb614c3b4656017660284903c9185dafee1601b3a370535b8ca"
    )
