"""NDJSON event emitter for native UI clients."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO


class EventEmitter:
    """Write one JSON object per line to a stream (default stdout)."""

    def __init__(self, stream: TextIO | None = None, enabled: bool = True):
        self.stream = stream or sys.stdout
        self.enabled = enabled

    def emit(self, event: str, **payload: Any) -> None:
        if not self.enabled:
            return
        row = {"event": event, **payload}
        self.stream.write(json.dumps(row, default=str) + "\n")
        self.stream.flush()

    def log(self, message: str, level: str = "info") -> None:
        self.emit("log", level=level, message=message)

    def progress(
        self,
        phase: str,
        *,
        clip: int | None = None,
        total: int | None = None,
        fraction: float | None = None,
    ) -> None:
        self.emit(
            "progress",
            phase=phase,
            clip=clip,
            total=total,
            fraction=fraction,
        )
