"""Journalisation structurée : ``clé=valeur`` (job_id, movie_id, segment, pid...)."""

from __future__ import annotations

import logging
import os
import sys

_FIELDS = ("job_id", "movie_id", "segment", "pid", "elapsed_ms")


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        when = self.formatTime(record, '%Y-%m-%d %H:%M:%S')
        base = f"{when} {record.levelname:<7} {record.name}: {record.getMessage()}"
        extras = [f"{k}={getattr(record, k)}" for k in _FIELDS if hasattr(record, k)]
        extras.append(f"proc={os.getpid()}")
        line = base + " | " + " ".join(extras)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger("movieshazam")
    if any(isinstance(h.formatter, KeyValueFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(KeyValueFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
