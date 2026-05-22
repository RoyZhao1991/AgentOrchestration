"""Local artifact download cache with digest validation."""

import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Dict, Tuple


Downloader = Callable[[Path], None]


class ArtifactCacheError(RuntimeError):
    """Base error for artifact cache failures."""


class DigestMismatchError(ArtifactCacheError, ValueError):
    """Raised when downloaded content does not match the expected digest."""


class ArtifactDownloadCache:
    """Cache downloaded artifacts and verify sha256 digests before reuse."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def fetch(
        self,
        cache_key: str,
        expected_digest: str,
        downloader: Downloader,
    ) -> Path:
        algorithm, digest = self._normalize_digest(expected_digest)
        artifact_path, metadata_path = self._paths(cache_key)

        if self._valid_hit(artifact_path, metadata_path, algorithm, digest):
            return artifact_path

        self._evict(artifact_path, metadata_path)
        temp_path = artifact_path.with_suffix(".artifact.part")
        if temp_path.exists():
            temp_path.unlink()

        try:
            downloader(temp_path)
            self._verify_download(temp_path, algorithm, digest)
            os.replace(temp_path, artifact_path)
            self._write_metadata(
                metadata_path,
                cache_key,
                algorithm,
                digest,
                artifact_path.stat().st_size,
            )
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        return artifact_path

    def _paths(self, cache_key: str) -> Tuple[Path, Path]:
        cache_id = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        artifact_path = self.root / f"{cache_id}.artifact"
        return artifact_path, artifact_path.with_suffix(".json")

    def _valid_hit(
        self,
        artifact_path: Path,
        metadata_path: Path,
        algorithm: str,
        digest: str,
    ) -> bool:
        if not artifact_path.exists() or not metadata_path.exists():
            return False

        metadata = self._read_metadata(metadata_path)
        if not metadata:
            return False

        if metadata.get("algorithm") != algorithm:
            return False
        if metadata.get("digest") != digest:
            return False
        if metadata.get("size") != artifact_path.stat().st_size:
            return False

        return self._digest_file(artifact_path, algorithm) == digest

    def _verify_download(
        self,
        path: Path,
        algorithm: str,
        expected_digest: str,
    ) -> None:
        if not path.exists():
            raise ArtifactCacheError("Downloader did not create an artifact")

        actual_digest = self._digest_file(path, algorithm)
        if actual_digest != expected_digest:
            raise DigestMismatchError(
                f"Artifact digest mismatch: expected {expected_digest}, "
                f"got {actual_digest}"
            )

    def _write_metadata(
        self,
        metadata_path: Path,
        cache_key: str,
        algorithm: str,
        digest: str,
        size: int,
    ) -> None:
        payload = {
            "algorithm": algorithm,
            "cache_key": cache_key,
            "digest": digest,
            "size": size,
        }
        temp_path = metadata_path.with_suffix(".json.part")
        temp_path.write_text(
            json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temp_path, metadata_path)

    def _read_metadata(self, metadata_path: Path) -> Dict:
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _evict(self, artifact_path: Path, metadata_path: Path) -> None:
        for path in (artifact_path, metadata_path):
            if path.exists():
                path.unlink()

    def _normalize_digest(self, expected_digest: str) -> Tuple[str, str]:
        if ":" in expected_digest:
            algorithm, digest = expected_digest.split(":", 1)
        else:
            algorithm, digest = "sha256", expected_digest

        algorithm = algorithm.lower()
        digest = digest.lower()
        if algorithm != "sha256":
            raise ValueError("Only sha256 artifact digests are supported")
        if not digest:
            raise ValueError("Expected artifact digest cannot be empty")
        return algorithm, digest

    def _digest_file(self, path: Path, algorithm: str) -> str:
        if algorithm != "sha256":
            raise ValueError("Only sha256 artifact digests are supported")

        hasher = hashlib.sha256()
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
