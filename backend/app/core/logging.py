from __future__ import annotations

import logging
import os
import sys

_FIELDS = ("job_id", "movie_id", "segment", "pid", "elapsed_ms")


class ConsoleFormatter(logging.Formatter):
    """Une ligne lisible par événement : « heure niveau [thread] message ».

    Les champs techniques passés en ``extra=`` (job_id, movie_id, segment, pid...) ne sont ajoutés
    qu'aux avertissements et aux erreurs, où ils aident au diagnostic.
    """

    def format(self, record: logging.LogRecord) -> str:
        when = self.formatTime(record, "%H:%M:%S")
        line = f"{when} {record.levelname:<7} [{record.threadName}] {record.getMessage()}"
        if record.levelno >= logging.WARNING:
            extras = [f"{k}={getattr(record, k)}" for k in _FIELDS if hasattr(record, k)]
            extras.append(f"proc={os.getpid()}")
            line += " | " + " ".join(extras)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger("movieshazam")
    if any(isinstance(h.formatter, ConsoleFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ConsoleFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    # Le frontend interroge l'API toutes les 1 à 2 s : sans ce réglage, les lignes d'accès d'Uvicorn
    # masqueraient les événements d'indexation. Les exceptions non gérées restent journalisées (uvicorn.error).
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
