"""Path sandboxing. Every filesystem write goes through here first."""
from __future__ import annotations

import os
from pathlib import Path


class Denied(Exception):
    """Raised when a path falls outside the allowed roots."""


def _real(p: str | Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def resolve_in_roots(path: str | Path, roots: list[str]) -> Path:
    """Resolve `path` and confirm it sits inside one of `roots`.

    Symlinks are resolved first, so a link pointing out of the sandbox is rejected.
    """
    target = _real(path)
    for root in roots:
        r = _real(root)
        if target == r or r in target.parents:
            return target
    raise Denied(
        f"{target} is outside the allowed folders. mac-chat may only touch: {', '.join(roots)}"
    )


def is_in_roots(path: str | Path, roots: list[str]) -> bool:
    try:
        resolve_in_roots(path, roots)
        return True
    except Denied:
        return False


def unique_destination(dst: Path) -> Path:
    """Never overwrite: append ' (1)', ' (2)', ... until the name is free."""
    if not dst.exists():
        return dst
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    for i in range(1, 1000):
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    raise Denied(f"could not find a free name near {dst}")
