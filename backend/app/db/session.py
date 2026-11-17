"""Moteur SQLAlchemy + sessions.

* Une connexion SQLite n'est jamais partagée arbitrairement : chaque opération ouvre une
  ``Session`` courte (``with session_factory() as s``) qui emprunte une connexion au pool.
* ``journal_mode=WAL`` : les lectures (API, recherche) ne bloquent pas l'écrivain et
  inversement ; il n'y a qu'**un seul écrivain** (``DbWriter``), donc pas de contention en écriture.
* ``foreign_keys=ON`` : suppressions en cascade cohérentes (film -> segments -> empreintes -> bandes).
* Les processus du pool n'ouvrent **jamais** la base : ils renvoient des résultats compacts.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_size=8,
        max_overflow=8,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    return engine


def init_db(engine: Engine) -> None:
    """Crée les tables manquantes (initialisation de schéma documentée, idempotente)."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
