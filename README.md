# mac-chat

A chatbot that runs entirely on your Mac and can talk about — and tidy up — your files.
No API keys, no network calls. The model is local (Ollama), the index is a local SQLite
database, and every file it reads stays on the machine.

## Setup

Requires macOS, Python 3.11+, and [Ollama](https://ollama.com).

```bash
brew install ollama && brew services start ollama
ollama pull qwen3:8b

git clone https://github.com/amirul-ammar/mac-chat.git
cd mac-chat
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ln -s "$PWD/mac-chat" /opt/homebrew/bin/mac-chat   # optional: put it on PATH
```

First run builds the index, which takes a few minutes depending on how much you have.

## Running it

```
mac-chat
```

Useful flags:

| Command | What it does |
| --- | --- |
| `mac-chat` | start chatting |
| `mac-chat --index` | update the index (only changed files) |
| `mac-chat --reindex` | rebuild the index from scratch |
| `mac-chat --ask "..."` | one question, one answer, exit |
| `mac-chat --think` | let the model reason first — slower, better on hard tasks |
| `mac-chat --model llama3.1:8b` | use a different Ollama model |

In-chat commands: `/index` `/reindex` `/stats` `/undo` `/think on|off` `/model` `/roots`
`/clear` `/help` `/quit`.

## What it can see

By default:

- `~/Documents`
- `~/Desktop`
- `~/Downloads`

`~/.mac-chat/config.json` is written on first run and is the place to change this. The keys
worth editing are `roots` (which folders it can see), `model` (any installed Ollama model),
and `num_ctx` (context window — raise it if you have RAM to spare, lower it if replies are
slow). Delete the file to get the defaults back. If it is unparseable, mac-chat falls back to
the defaults and leaves your file alone rather than overwriting it.

Nothing outside those folders can be read or written — paths are resolved through symlinks
and checked against the allowed roots before any operation.

The index skips noise: `node_modules`, `.git`, `Library`, virtualenvs, build output, caches,
app bundles, photo libraries, and hidden files.

Text is extracted from PDF, Word, Excel, PowerPoint, RTF, Markdown, CSV, JSON, and source
code, so you can search *inside* documents, not just by filename.

## Safety model

- **Reads are free.** Searching, listing, reading, and reporting need no approval.
- **Every write is staged.** The model can only call `propose_changes`, which stages a plan.
  You see a table of every operation and answer `y`, `n`, or `s` to skip specific lines.
- **Nothing is ever hard-deleted.** "Trash" moves files to `~/.Trash`.
- **Nothing is overwritten.** A name collision becomes `file (1).pdf`.
- **`/undo`** reverses the last applied batch, using a journal kept in the database.

## Things to ask

```
what's taking up the most space in Downloads?
find every PDF I touched in the last month
which of my files mention "lakehouse"?
summarise ~/Documents/notes.md
sort my Downloads folder into subfolders by type
find duplicate files in Documents
what have I not opened in over a year?
```

## Layout

```
macchat/config.py    roots, skip rules, file-type map
macchat/db.py        SQLite schema, FTS5 index, operation journal
macchat/extract.py   text extraction per file type
macchat/indexer.py   the filesystem walk
macchat/safety.py    path sandbox — every write passes through here
macchat/tools.py     the seven tools the model can call
macchat/schemas.py   tool definitions + system prompt
macchat/ollama.py    local HTTP client
macchat/cli.py       REPL, tool loop, approval prompts
```

Data lives in `~/.mac-chat/` (`index.db`, `config.json`).

## Requirements

Ollama must be running: `brew services start ollama`. Default model is `qwen3:8b` (about
5 GB, needs roughly 6 GB of RAM while loaded). Any Ollama model with tool-calling support
works — switch with `/model` or `--model`.

Note that an 8B model is solid at search and summarising but weaker at long multi-step
reasoning. `/think on` trades speed for better results on harder requests.
