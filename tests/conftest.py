"""Shared fixtures. Every test runs against a throwaway root and a throwaway database,
so nothing here can touch the real index or the real filesystem."""
import pytest

from macchat import config, tools


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A sandboxed Session: one allowed root, an isolated DB, an isolated trash."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "index.db")

    root = tmp_path / "root"
    root.mkdir()
    trash = tmp_path / "trash"
    trash.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    cfg = dict(config.DEFAULTS)
    cfg["roots"] = [str(root)]
    cfg["trash_dir"] = str(trash)

    session = tools.Session(cfg)
    yield session, root, trash, outside
    session.close()


@pytest.fixture
def sample_files(workspace):
    """Populate the root with one file of each category."""
    session, root, trash, outside = workspace
    files = {
        "notes.md": "meeting notes about the budget",
        "data.csv": "a,b,c\n1,2,3",
        "script.py": "print('hello')",
        "photo.png": "\x89PNG fake",
        "clip.mp4": "fake video",
    }
    for name, body in files.items():
        (root / name).write_text(body)
    return session, root, trash, outside
