"""Append-safe JSONL event logging."""

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union


class JSONLEventLogWriter:
    def __init__(
        self,
        path: Union[str, Path],
        max_bytes: Optional[int] = None,
        lock_path: Optional[Union[str, Path]] = None,
    ):
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.lock_path = (
            Path(lock_path)
            if lock_path is not None
            else self.path.with_name(f"{self.path.name}.lock")
        )
        self.malformed_records = 0

    def append(self, event: Dict[str, Any]) -> None:
        payload = self._encode_event(event)
        with self._file_lock(exclusive=True):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed(len(payload))
            self._append_bytes(payload)

    def read_events(
        self,
        include_rotated: bool = True,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        malformed = 0
        with self._file_lock(exclusive=False):
            for path in self.log_paths(include_rotated=include_rotated):
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.endswith("\n"):
                            malformed += 1
                            continue
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            records.append(json.loads(stripped))
                        except json.JSONDecodeError:
                            malformed += 1
        self.malformed_records = malformed
        return records

    def malformed_record_count(self, include_rotated: bool = True) -> int:
        self.read_events(include_rotated=include_rotated)
        return self.malformed_records

    def log_paths(self, include_rotated: bool = True) -> List[Path]:
        paths: List[Path] = []
        if include_rotated and self.path.parent.exists():
            paths.extend(
                sorted(
                    self.path.parent.glob(f"{self.path.name}.*"),
                    key=self._rotation_sort_key,
                )
            )
        if self.path.exists():
            paths.append(self.path)
        return [path for path in paths if path != self.lock_path]

    def _encode_event(self, event: Dict[str, Any]) -> bytes:
        return (
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if self.max_bytes is None or not self.path.exists():
            return
        current_size = self.path.stat().st_size
        if current_size == 0:
            return
        if current_size + incoming_bytes <= self.max_bytes:
            return
        os.replace(self.path, self._next_rotation_path())

    def _append_bytes(self, payload: bytes) -> None:
        fd = os.open(
            str(self.path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            remaining = payload
            while remaining:
                written = os.write(fd, remaining)
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _next_rotation_path(self) -> Path:
        for index in range(1, 1_000_000):
            candidate = self.path.with_name(f"{self.path.name}.{index}")
            if not candidate.exists():
                return candidate
        raise RuntimeError("unable to allocate rotated log path")

    @contextmanager
    def _file_lock(self, exclusive: bool) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock_file:
            lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lock_file.fileno(), lock_type)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _rotation_sort_key(self, path: Path) -> int:
        suffix = path.name.removeprefix(f"{self.path.name}.")
        return int(suffix) if suffix.isdigit() else 1_000_000
