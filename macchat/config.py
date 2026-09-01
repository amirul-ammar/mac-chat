"""Configuration for mac-chat. Everything lives locally; nothing is sent off the machine."""
from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path.home()
DATA_DIR = HOME / ".mac-chat"
DB_PATH = DATA_DIR / "index.db"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "roots": [
        str(HOME / "Documents"),
        str(HOME / "Desktop"),
        str(HOME / "Downloads"),
    ],
    "model": "qwen3:8b",
    "ollama_url": "http://localhost:11434",
    "num_ctx": 16384,
    "max_file_mb_for_text": 25,
    "max_text_chars": 200_000,
    "trash_dir": str(HOME / ".Trash"),
}

# Directory names never walked into.
SKIP_DIRS = {
    "node_modules", ".git", ".hg", ".svn", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache",
    "dist", "build", "out", "target", "vendor", "Pods", ".gradle",
    ".terraform", "site-packages", "DerivedData", ".cache", ".npm",
    ".pnpm-store", ".yarn", "Library", ".Trash", ".mac-chat",
    "bower_components", "coverage", ".idea", ".tox", ".eggs",
}

# Directory suffixes that are really opaque bundles.
SKIP_DIR_SUFFIXES = (
    ".app", ".framework", ".bundle", ".xcodeproj", ".xcworkspace",
    ".photoslibrary", ".fcpbundle", ".imovielibrary", ".tvlibrary",
    ".aplibrary", ".musiclibrary", ".sparsebundle", ".lrcat",
)

SKIP_FILES = {".DS_Store", ".localized", "Icon\r"}

KIND_BY_EXT = {
    "document": {".pdf", ".doc", ".docx", ".pages", ".rtf", ".odt", ".txt",
                 ".md", ".markdown", ".rst", ".tex", ".epub"},
    "spreadsheet": {".xls", ".xlsx", ".numbers", ".csv", ".tsv", ".ods"},
    "presentation": {".ppt", ".pptx", ".key", ".odp"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".webp",
              ".tiff", ".tif", ".bmp", ".svg", ".raw", ".cr2", ".nef", ".dng", ".psd", ".ai"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm", ".mpg", ".mpeg", ".wmv"},
    "audio": {".mp3", ".m4a", ".wav", ".aac", ".flac", ".aiff", ".ogg", ".opus"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".dmg", ".pkg", ".iso"},
    "code": {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
             ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".kts", ".scala",
             ".sh", ".bash", ".zsh", ".fish", ".sql", ".r", ".m", ".mm", ".lua",
             ".pl", ".vue", ".svelte", ".dart", ".ipynb"},
    "data": {".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf",
             ".parquet", ".db", ".sqlite", ".sqlite3", ".plist", ".log"},
    "font": {".ttf", ".otf", ".woff", ".woff2"},
}

EXT_TO_KIND = {ext: kind for kind, exts in KIND_BY_EXT.items() for ext in exts}


def kind_for(ext: str) -> str:
    return EXT_TO_KIND.get(ext.lower(), "other")


def load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    existed = CONFIG_PATH.exists()
    if existed:
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except (json.JSONDecodeError, OSError):
            pass  # unreadable or corrupt: fall back to defaults, keep the user's file
    cfg["roots"] = [str(Path(r).expanduser().resolve()) for r in cfg["roots"]]

    if not existed:
        # First run: write the working config out so there is a real file to edit.
        try:
            save(cfg)
        except OSError:
            pass  # read-only home: carry on with in-memory defaults

    # Applied after the write so a one-off override never gets baked into the file.
    env_model = os.environ.get("MACCHAT_MODEL")
    if env_model:
        cfg["model"] = env_model
    return cfg


def save(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
