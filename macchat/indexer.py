"""Walks the allowed roots and builds the local index."""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import config, db, extract


def _skip_dir(name: str) -> bool:
    if name.startswith("."):
        return True
    if name in config.SKIP_DIRS:
        return True
    return name.endswith(config.SKIP_DIR_SUFFIXES)


def walk(roots: list[str]):
    """Yield every indexable file path under `roots`, pruning noise directories."""
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
            dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
            for fn in filenames:
                if fn.startswith(".") or fn in config.SKIP_FILES:
                    continue
                yield Path(dirpath) / fn


def build(cfg: dict, full: bool = False, progress=None) -> dict:
    """Index the roots. Incremental by default: unchanged files are skipped.

    Returns counts of what happened.
    """
    conn = db.connect()
    roots = cfg["roots"]
    max_bytes = cfg["max_file_mb_for_text"] * 1024 * 1024
    limit = cfg["max_text_chars"]

    known: dict[str, tuple[float, int]] = {}
    if not full:
        for r in conn.execute("SELECT path, mtime, size FROM files"):
            known[r["path"]] = (r["mtime"], r["size"])
    else:
        conn.execute("DELETE FROM docs_fts")
        conn.execute("DELETE FROM files")
        conn.commit()

    seen: set[str] = set()
    stats = {"scanned": 0, "added": 0, "updated": 0, "text": 0, "removed": 0}
    pending = 0

    for path in walk(roots):
        try:
            st = path.stat()
        except OSError:
            continue
        stats["scanned"] += 1
        sp = str(path)
        seen.add(sp)

        prior = known.get(sp)
        if prior and abs(prior[0] - st.st_mtime) < 1e-6 and prior[1] == st.st_size:
            if progress and stats["scanned"] % 500 == 0:
                progress(stats)
            continue

        body = None
        ext = path.suffix.lower()
        if extract.can_extract(ext) and st.st_size <= max_bytes:
            body = extract.extract(path, limit)
            if body:
                stats["text"] += 1

        try:
            db.upsert_file(conn, path, st, body)
        except Exception:
            continue
        stats["updated" if prior else "added"] += 1
        pending += 1

        if pending >= 400:
            conn.commit()
            pending = 0
        if progress and stats["scanned"] % 500 == 0:
            progress(stats)

    conn.commit()

    # Drop records for files that no longer exist.
    for r in conn.execute("SELECT path FROM files").fetchall():
        if r["path"] not in seen and not Path(r["path"]).exists():
            db.forget(conn, r["path"])
            stats["removed"] += 1

    db.set_meta(conn, "last_index", str(time.time()))
    db.set_meta(conn, "roots", "\n".join(roots))
    conn.commit()
    conn.close()
    if progress:
        progress(stats)
    return stats
