"""Tools the model can call. Reads are free; every write is staged for your approval."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import config, db, extract, safety

MAX_ROWS = 60


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def human_age(mtime: float) -> str:
    days = (time.time() - mtime) / 86400
    if days < 1:
        return "today"
    if days < 30:
        return f"{days:.0f}d ago"
    if days < 365:
        return f"{days / 30:.0f}mo ago"
    return f"{days / 365:.1f}y ago"


def short(path: str) -> str:
    home = str(Path.home())
    return path.replace(home, "~", 1) if path.startswith(home) else path


def fts_query(raw: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression."""
    terms = []
    for tok in raw.replace('"', " ").split():
        cleaned = "".join(c for c in tok if c.isalnum() or c in "-_.@")
        if len(cleaned) >= 2:
            terms.append(f'"{cleaned}"')
    return " ".join(terms)


@dataclass
class Plan:
    summary: str
    operations: list[dict]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class Session:
    """Holds the DB connection, config, and any plan awaiting approval."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.conn = db.connect()
        self.roots: list[str] = cfg["roots"]
        self.pending: Plan | None = None

    def close(self) -> None:
        self.conn.close()

    # ---------- read tools ----------

    def search_files(self, query="", kind="", ext="", folder="", modified_within_days=0,
                     min_size_mb=0.0, sort_by="relevance", limit=25) -> str:
        sql = ["SELECT path, name, size, mtime, kind FROM files WHERE 1=1"]
        args: list = []

        for tok in str(query).split():
            sql.append("AND (name LIKE ? OR path LIKE ?)")
            args += [f"%{tok}%", f"%{tok}%"]
        if kind:
            sql.append("AND kind = ?")
            args.append(str(kind).lower())
        if ext:
            e = str(ext).lower()
            sql.append("AND ext = ?")
            args.append(e if e.startswith(".") else "." + e)
        if folder:
            try:
                f = safety.resolve_in_roots(folder, self.roots)
            except safety.Denied as exc:
                return f"Error: {exc}"
            sql.append("AND path LIKE ?")
            args.append(f"{f}/%")
        if modified_within_days:
            sql.append("AND mtime >= ?")
            args.append(time.time() - float(modified_within_days) * 86400)
        if min_size_mb:
            sql.append("AND size >= ?")
            args.append(float(min_size_mb) * 1024 * 1024)

        order = {
            "newest": "mtime DESC", "modified": "mtime DESC", "oldest": "mtime ASC",
            "largest": "size DESC", "size": "size DESC", "smallest": "size ASC",
            "name": "name ASC",
        }.get(str(sort_by).lower(), "mtime DESC")
        sql.append(f"ORDER BY {order} LIMIT ?")
        args.append(min(int(limit or 25), MAX_ROWS))

        rows = self.conn.execute(" ".join(sql), args).fetchall()
        if not rows:
            return "No files matched."
        lines = [f"{len(rows)} match(es):"]
        for r in rows:
            lines.append(f"- {short(r['path'])}  [{human_size(r['size'])}, {human_age(r['mtime'])}]")
        return "\n".join(lines)

    def search_content(self, query="", folder="", limit=15) -> str:
        q = fts_query(str(query))
        if not q:
            return "Error: give at least one search word of 2+ characters."
        sql = (
            "SELECT f.path, f.size, f.mtime, "
            "  snippet(docs_fts, 0, '<<', '>>', ' … ', 14) AS snip "
            "FROM docs_fts JOIN files f ON f.id = docs_fts.rowid "
            "WHERE docs_fts MATCH ? "
        )
        args: list = [q]
        if folder:
            try:
                f = safety.resolve_in_roots(folder, self.roots)
            except safety.Denied as exc:
                return f"Error: {exc}"
            sql += "AND f.path LIKE ? "
            args.append(f"{f}/%")
        sql += "ORDER BY bm25(docs_fts) LIMIT ?"
        args.append(min(int(limit or 15), 30))

        try:
            rows = self.conn.execute(sql, args).fetchall()
        except Exception as exc:
            return f"Error running search: {exc}"
        if not rows:
            return f"Nothing in the indexed text matched {query!r}."
        lines = [f"{len(rows)} file(s) containing {query!r}:"]
        for r in rows:
            snip = " ".join(str(r["snip"]).split())[:220]
            lines.append(f"- {short(r['path'])} ({human_age(r['mtime'])})\n    {snip}")
        return "\n".join(lines)

    def read_file(self, path="", max_chars=4000) -> str:
        try:
            p = safety.resolve_in_roots(path, self.roots)
        except safety.Denied as exc:
            return f"Error: {exc}"
        if not p.exists():
            return f"Error: {short(str(p))} does not exist."
        if p.is_dir():
            return f"Error: {short(str(p))} is a folder — use list_folder."
        limit = max(200, min(int(max_chars or 4000), 20000))
        text = extract.extract(p, limit * 2)
        if not text:
            st = p.stat()
            return (f"{short(str(p))} has no extractable text "
                    f"({p.suffix or 'no extension'}, {human_size(st.st_size)}, "
                    f"modified {human_age(st.st_mtime)}).")
        out = text[:limit]
        if len(text) > limit:
            out += f"\n… [truncated, {len(text)} chars extracted]"
        return f"--- {short(str(p))} ---\n{out}"

    def list_folder(self, path="", limit=50) -> str:
        try:
            p = safety.resolve_in_roots(path, self.roots)
        except safety.Denied as exc:
            return f"Error: {exc}"
        if not p.is_dir():
            return f"Error: {short(str(p))} is not a folder."
        dirs, files = [], []
        try:
            for entry in os.scandir(p):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(entry.name)
                else:
                    try:
                        st = entry.stat()
                        files.append((entry.name, st.st_size, st.st_mtime))
                    except OSError:
                        continue
        except OSError as exc:
            return f"Error reading folder: {exc}"

        n = min(int(limit or 50), MAX_ROWS)
        lines = [f"{short(str(p))} — {len(dirs)} folder(s), {len(files)} file(s)"]
        for d in sorted(dirs)[:n]:
            lines.append(f"  [dir]  {d}/")
        for name, size, mtime in sorted(files, key=lambda x: -x[2])[:n]:
            lines.append(f"  file   {name}  ({human_size(size)}, {human_age(mtime)})")
        if len(dirs) + len(files) > n * 2:
            lines.append("  … truncated")
        return "\n".join(lines)

    def folder_report(self, path="") -> str:
        try:
            p = safety.resolve_in_roots(path, self.roots) if path else None
        except safety.Denied as exc:
            return f"Error: {exc}"
        where, args = "", []
        if p:
            where = "WHERE path LIKE ?"
            args = [f"{p}/%"]

        total = self.conn.execute(
            f"SELECT COUNT(*) c, COALESCE(SUM(size),0) s FROM files {where}", args
        ).fetchone()
        if not total["c"]:
            return "No indexed files there. Run /index if this folder is new."

        lines = [f"{short(str(p)) if p else 'All indexed folders'}: "
                 f"{total['c']} files, {human_size(total['s'])} total", "", "By kind:"]
        for r in self.conn.execute(
            f"SELECT kind, COUNT(*) c, COALESCE(SUM(size),0) s FROM files {where} "
            "GROUP BY kind ORDER BY c DESC", args
        ):
            lines.append(f"  {r['kind']:<13} {r['c']:>6} files  {human_size(r['s'])}")

        lines += ["", "Top extensions:"]
        ext_where = f"{where} AND ext != ''" if where else "WHERE ext != ''"
        for r in self.conn.execute(
            f"SELECT ext, COUNT(*) c FROM files {ext_where} "
            "GROUP BY ext ORDER BY c DESC LIMIT 10", args
        ):
            lines.append(f"  {r['ext']:<13} {r['c']:>6}")

        lines += ["", "Largest:"]
        for r in self.conn.execute(
            f"SELECT path, size FROM files {where} ORDER BY size DESC LIMIT 5", args
        ):
            lines.append(f"  {human_size(r['size']):>9}  {short(r['path'])}")

        stale = self.conn.execute(
            f"SELECT COUNT(*) c FROM files {where} {'AND' if where else 'WHERE'} mtime < ?",
            args + [time.time() - 365 * 86400],
        ).fetchone()["c"]
        lines += ["", f"Untouched for over a year: {stale} files"]

        subs = self.conn.execute(
            f"SELECT parent, COUNT(*) c FROM files {where} GROUP BY parent ORDER BY c DESC LIMIT 8",
            args,
        ).fetchall()
        if subs:
            lines += ["", "Busiest subfolders:"]
            for r in subs:
                lines.append(f"  {r['c']:>6}  {short(r['parent'])}")
        return "\n".join(lines)

    def find_duplicates(self, folder="", min_size_mb=0.1, limit=15) -> str:
        where, args = "", []
        if folder:
            try:
                f = safety.resolve_in_roots(folder, self.roots)
            except safety.Denied as exc:
                return f"Error: {exc}"
            where = "AND path LIKE ?"
            args = [f"{f}/%"]
        min_bytes = max(1024, int(float(min_size_mb or 0.1) * 1024 * 1024))

        groups = self.conn.execute(
            f"SELECT size, COUNT(*) c FROM files WHERE size >= ? {where} "
            "GROUP BY size HAVING c > 1 ORDER BY size DESC LIMIT 400",
            [min_bytes] + args,
        ).fetchall()
        if not groups:
            return "No same-size candidates, so no duplicates."

        found: list[tuple[int, list[str]]] = []
        for g in groups:
            rows = self.conn.execute(
                f"SELECT path FROM files WHERE size = ? {where}", [g["size"]] + args
            ).fetchall()
            by_hash: dict[str, list[str]] = defaultdict(list)
            for r in rows:
                h = self._quick_hash(Path(r["path"]))
                if h:
                    by_hash[h].append(r["path"])
            for paths in by_hash.values():
                if len(paths) > 1:
                    found.append((g["size"], sorted(paths)))
            if len(found) >= int(limit or 15):
                break

        if not found:
            return "No duplicate content found (same-size files differed)."
        wasted = sum(size * (len(paths) - 1) for size, paths in found)
        lines = [f"{len(found)} duplicate group(s), {human_size(wasted)} recoverable:"]
        for size, paths in found[: int(limit or 15)]:
            lines.append(f"\n  {human_size(size)} × {len(paths)}:")
            for pth in paths:
                lines.append(f"    {short(pth)}")
        lines.append("\nKeep the first of each group unless the user says otherwise.")
        return "\n".join(lines)

    @staticmethod
    def _quick_hash(p: Path) -> str | None:
        """Hash head + tail + size — fast, and strong enough to pair with equal size."""
        try:
            size = p.stat().st_size
            h = hashlib.blake2b(str(size).encode(), digest_size=16)
            with p.open("rb") as fh:
                h.update(fh.read(65536))
                if size > 131072:
                    fh.seek(-65536, os.SEEK_END)
                    h.update(fh.read(65536))
            return h.hexdigest()
        except OSError:
            return None

    # ---------- write tool (staged, never immediate) ----------

    def _resolve_src(self, raw: str):
        """Resolve a source path. Falls back to a case-insensitive name match in the same
        folder, which rescues plans where the model got the capitalisation slightly wrong."""
        p = safety.resolve_in_roots(raw, self.roots)
        if p.exists():
            return p
        parent, target = p.parent, p.name.lower()
        if parent.is_dir():
            matches = [c for c in parent.iterdir() if c.name.lower() == target]
            if len(matches) == 1:
                return matches[0]
        return None

    def propose_changes(self, summary="", operations=None) -> str:
        ops = operations or []
        if isinstance(ops, dict):
            ops = [ops]
        if not isinstance(ops, list) or not ops:
            return "Error: operations must be a non-empty list."
        if len(ops) > 300:
            return "Error: at most 300 operations per plan. Propose it in batches."

        clean, problems = [], []
        for i, op in enumerate(ops, 1):
            if not isinstance(op, dict):
                problems.append(f"op {i}: not an object")
                continue
            action = str(op.get("action", "")).lower().strip()
            src = str(op.get("src", "") or "").strip()
            dst = str(op.get("dst", "") or "").strip()
            if action in ("rename", "mv"):
                action = "move"
            if action in ("delete", "rm"):
                action = "trash"
            if action in ("mkdir", "create_folder"):
                action = "new_folder"
            if action not in ("move", "trash", "new_folder"):
                problems.append(f"op {i}: unknown action {action!r} (use move, trash, new_folder)")
                continue
            try:
                if action == "new_folder":
                    target = safety.resolve_in_roots(dst or src, self.roots)
                    clean.append({"action": action, "src": "", "dst": str(target)})
                    continue
                s = self._resolve_src(src)
                if s is None:
                    problems.append(f"op {i}: {short(src)} does not exist")
                    continue
                if action == "trash":
                    clean.append({"action": "trash", "src": str(s), "dst": ""})
                else:
                    if not dst:
                        problems.append(f"op {i}: move needs a dst")
                        continue
                    d = safety.resolve_in_roots(dst, self.roots)
                    if d.is_dir():
                        d = d / s.name
                    clean.append({"action": "move", "src": str(s), "dst": str(d)})
            except safety.Denied as exc:
                problems.append(f"op {i}: {exc}")

        if not clean:
            return "Nothing staged. Problems:\n" + "\n".join(problems)

        self.pending = Plan(summary=str(summary) or "file changes", operations=clean)
        out = [f"Staged {len(clean)} operation(s) as plan {self.pending.id}. "
               "AWAITING USER APPROVAL — nothing has moved yet."]
        if problems:
            out.append("Skipped:\n" + "\n".join(problems))
        out.append("Now tell the user in plain language what you propose and why.")
        return "\n".join(out)

    KIND_FOLDER = {
        "document": "Documents", "spreadsheet": "Spreadsheets",
        "presentation": "Presentations", "image": "Images", "video": "Video",
        "audio": "Audio", "archive": "Archives", "code": "Code",
        "data": "Data", "font": "Fonts", "other": "Other",
    }

    def propose_sort(self, folder="", scheme="type", limit=400) -> str:
        """Build a tidy-up plan in code — no filenames are retyped, so nothing is mangled."""
        try:
            root = safety.resolve_in_roots(folder, self.roots)
        except safety.Denied as exc:
            return f"Error: {exc}"
        if not root.is_dir():
            return f"Error: {short(str(root))} is not a folder."

        scheme = str(scheme).lower().strip()
        if scheme in ("kind", "file type", "filetype"):
            scheme = "type"
        if scheme not in ("type", "extension", "date"):
            return "Error: scheme must be 'type', 'extension', or 'date'."

        buckets: dict[str, list[Path]] = defaultdict(list)
        for entry in sorted(root.iterdir(), key=lambda e: e.name.lower()):
            if entry.name.startswith(".") or not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if scheme == "type":
                bucket = self.KIND_FOLDER.get(config.kind_for(ext), "Other")
            elif scheme == "extension":
                bucket = ext.lstrip(".").upper() or "No extension"
            else:
                bucket = time.strftime("%Y-%m", time.localtime(entry.stat().st_mtime))
            buckets[bucket].append(entry)

        if not buckets:
            return f"{short(str(root))} has no loose files to sort."

        ops: list[dict] = []
        preview: list[str] = []
        for bucket in sorted(buckets):
            files = buckets[bucket]
            dest = root / bucket
            if not dest.exists():
                ops.append({"action": "new_folder", "src": "", "dst": str(dest)})
            for f in files:
                if len(ops) >= int(limit or 400):
                    break
                ops.append({"action": "move", "src": str(f), "dst": str(dest / f.name)})
            preview.append(f"  {bucket}/ — {len(files)} file(s): "
                           + ", ".join(f.name for f in files[:4])
                           + (" …" if len(files) > 4 else ""))

        moves = sum(1 for o in ops if o["action"] == "move")
        self.pending = Plan(
            summary=f"Sort {short(str(root))} by {scheme}", operations=ops
        )
        return (
            f"Staged a plan to sort {short(str(root))} by {scheme}: "
            f"{len(buckets)} folder(s), {moves} file(s) moved. "
            "AWAITING USER APPROVAL — nothing has moved yet.\n"
            + "\n".join(preview)
            + "\n\nNow describe this grouping to the user in one or two sentences."
        )

    # ---------- execution (called by the CLI after the user says yes) ----------

    def apply(self, plan: Plan) -> tuple[int, list[str]]:
        batch = plan.id
        done, errors = 0, []
        trash = Path(self.cfg["trash_dir"])
        for op in plan.operations:
            try:
                if op["action"] == "new_folder":
                    Path(op["dst"]).mkdir(parents=True, exist_ok=True)
                    dst = op["dst"]
                elif op["action"] == "trash":
                    src = Path(op["src"])
                    trash.mkdir(parents=True, exist_ok=True)
                    dst_path = safety.unique_destination(trash / src.name)
                    shutil.move(str(src), str(dst_path))
                    db.forget(self.conn, op["src"])
                    dst = str(dst_path)
                else:
                    src = Path(op["src"])
                    dst_path = safety.unique_destination(Path(op["dst"]))
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst_path))
                    db.move_file_record(self.conn, op["src"], str(dst_path))
                    dst = str(dst_path)
                self.conn.execute(
                    "INSERT INTO ops(batch, ts, action, src, dst, status) VALUES(?,?,?,?,?,?)",
                    (batch, time.time(), op["action"], op.get("src", ""), dst, "applied"),
                )
                done += 1
            except Exception as exc:
                errors.append(f"{short(op.get('src') or op.get('dst', ''))}: {exc}")
        self.conn.commit()
        return done, errors

    def undo_last(self) -> str:
        row = self.conn.execute(
            "SELECT batch FROM ops WHERE status='applied' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "Nothing to undo."
        batch = row["batch"]
        ops = self.conn.execute(
            "SELECT * FROM ops WHERE batch=? AND status='applied' ORDER BY id DESC", (batch,)
        ).fetchall()
        restored, errors = 0, []
        for op in ops:
            try:
                if op["action"] == "new_folder":
                    d = Path(op["dst"])
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                else:
                    dst, src = Path(op["dst"]), Path(op["src"])
                    if not dst.exists():
                        errors.append(f"{short(op['dst'])} is gone")
                        continue
                    back = safety.unique_destination(src)
                    back.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(back))
                    db.move_file_record(self.conn, op["dst"], str(back))
                self.conn.execute("UPDATE ops SET status='undone' WHERE id=?", (op["id"],))
                restored += 1
            except Exception as exc:
                errors.append(f"{short(op['dst'])}: {exc}")
        self.conn.commit()
        msg = f"Undid {restored} of {len(ops)} operation(s) from batch {batch}."
        if errors:
            msg += "\nProblems:\n  " + "\n  ".join(errors)
        return msg
