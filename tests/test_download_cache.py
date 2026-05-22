import hashlib

import pytest

from src.common.download_cache import (
    ArtifactDownloadCache,
    DigestMismatchError,
)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_cache_hit_verifies_digest_without_redownload(tmp_path):
    payload = b"release artifact"
    cache = ArtifactDownloadCache(tmp_path)
    calls = []

    def downloader(path):
        calls.append(path)
        path.write_bytes(payload)

    cached_path = cache.fetch("artifact.whl", _digest(payload), downloader)
    second_path = cache.fetch(
        "artifact.whl",
        f"sha256:{_digest(payload)}",
        lambda path: pytest.fail("cache hit should not download"),
    )

    assert cached_path == second_path
    assert second_path.read_bytes() == payload
    assert len(calls) == 1


def test_digest_mismatch_evicts_and_redownloads(tmp_path):
    payload = b"release artifact"
    corrupt_payload = b"Release artifact"
    cache = ArtifactDownloadCache(tmp_path)
    cache_path = cache.fetch(
        "artifact.whl",
        _digest(payload),
        lambda path: path.write_bytes(payload),
    )
    cache_path.write_bytes(corrupt_payload)
    calls = []

    def redownload(path):
        calls.append(path)
        path.write_bytes(payload)

    repaired_path = cache.fetch("artifact.whl", _digest(payload), redownload)

    assert repaired_path == cache_path
    assert repaired_path.read_bytes() == payload
    assert len(calls) == 1


def test_partial_cache_file_evicts_and_redownloads(tmp_path):
    payload = b"complete artifact payload"
    cache = ArtifactDownloadCache(tmp_path)
    cache_path = cache.fetch(
        "artifact.tar.gz",
        _digest(payload),
        lambda path: path.write_bytes(payload),
    )
    cache_path.write_bytes(payload[:7])
    calls = []

    def redownload(path):
        calls.append(path)
        path.write_bytes(payload)

    repaired_path = cache.fetch(
        "artifact.tar.gz",
        _digest(payload),
        redownload,
    )

    assert repaired_path.read_bytes() == payload
    assert len(calls) == 1


def test_download_digest_mismatch_is_not_cached(tmp_path):
    expected_payload = b"trusted artifact"
    wrong_payload = b"tampered artifact"
    cache = ArtifactDownloadCache(tmp_path)

    with pytest.raises(DigestMismatchError):
        cache.fetch(
            "artifact.tar.gz",
            _digest(expected_payload),
            lambda path: path.write_bytes(wrong_payload),
        )

    assert list(tmp_path.iterdir()) == []
