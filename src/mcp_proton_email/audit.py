"""Append-only JSONL audit log for every mutation. No message bodies (spec 7)."""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

ROTATE_BYTES = 5 * 1024 * 1024


class AuditLog:
    def __init__(self, directory: Path) -> None:
        self._path = directory / "audit.log"
        self._lock = threading.Lock()
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)

    @property
    def path(self) -> Path:
        return self._path

    def record(self, tool: str, status: str, **fields: object) -> None:
        entry: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "status": status,
        }
        entry.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            self._rotate_if_needed()
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            os.chmod(self._path, 0o600)

    def _rotate_if_needed(self) -> None:
        try:
            if self._path.stat().st_size >= ROTATE_BYTES:
                rotated = self._path.with_suffix(".log.1")
                self._path.replace(rotated)
        except FileNotFoundError:
            pass

    def tail(self, limit: int = 100) -> list[dict[str, object]]:
        with self._lock:
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                return []
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
