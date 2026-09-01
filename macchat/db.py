"""SQLite index: file metadata + FTS5 full-text over extracted content."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    parent      TEXT NOT NULL,
    ext         TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'other',
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL NOT NULL DEFAULT 0,
    indexed_at  REAL NOT NULL DEFAULT 0,
    has_text    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_name   ON files(name);
CREATE INDEX IF NOT EXISTS idx_files_ext    ON files(ext);
CREATE INDEX IF NOT EXISTS idx_files_kind   ON files(kind);
CREATE INDEX IF NOT EXISTS idx_files_mtime  ON files(mtime);
CREATE INDEX IF NOT EXISTS idx_files_size   ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_parent ON files(parent);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    body, name, path, tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS ops (
    id      INTEGER PRIMARY KEY,
    batch   TEXT NOT NULL,
    ts      REAL NOT NULL,
    action  TEXT NOT NULL,
    src     TEXT NOT NULL DEFAULT '',
    dst     TEXT NOT NULL DEFAULT '',
    status  TEXT NOT NULL DEFAULT 'applied',
    note    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ops_batch ON ops(batch);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def upsert_file(conn: sqlite3.Connection, path: Path, st, body: str | None) -> None:
    ext = path.suffix.lower()
    row = conn.execute(
        "INSERT INTO files(path, name, parent, ext, kind, size, mtime, indexed_at, has_text) "
        "VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "  name=excluded.name, parent=excluded.parent, ext=excluded.ext, kind=excluded.kind, "
        "  size=excluded.size, mtime=excluded.mtime, indexed_at=excluded.indexed_at, "
        "  has_text=excluded.has_text "
        "RETURNING id",
        (
            str(path), path.name, str(path.parent), ext, config.kind_for(ext),
            st.st_size, st.st_mtime, time.time(), 1 if body else 0,
        ),
    ).fetchone()
    fid = row["id"]
    conn.execute("DELETE FROM docs_fts WHERE rowid=?", (fid,))
    if body:
        conn.execute(
            "INSERT INTO docs_fts(rowid, body, name, path) VALUES(?,?,?,?)",
            (fid, body, path.name, str(path)),
        )


def move_file_record(conn: sqlite3.Connection, src: str, dst: str) -> None:
    row = conn.execute("SELECT id FROM files WHERE path=?", (src,)).fetchone()
    if not row:
        return
    p = Path(dst)
    conn.execute("DELETE FROM files WHERE path=?", (dst,))
    conn.execute(
        "UPDATE files SET path=?, name=?, parent=? WHERE id=?",
        (dst, p.name, str(p.parent), row["id"]),
    )
    conn.execute("UPDATE docs_fts SET name=?, path=? WHERE rowid=?", (p.name, dst, row["id"]))


def forget(conn: sqlite3.Connection, path: str) -> None:
    row = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    if row:
        conn.execute("DELETE FROM docs_fts WHERE rowid=?", (row["id"],))
        conn.execute("DELETE FROM files WHERE id=?", (row["id"],))
