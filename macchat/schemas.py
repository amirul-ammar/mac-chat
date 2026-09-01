"""Tool definitions handed to the model, in OpenAI/Ollama function-calling format."""

def _fn(name, description, properties, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


TOOLS = [
    _fn(
        "search_files",
        "Find files by name, type, size, or date from the local index. "
        "Use this for questions like 'where is my resume', 'what PDFs did I add last week', "
        "'biggest files in Downloads'. Does NOT look inside files — use search_content for that.",
        {
            "query": {"type": "string", "description": "Words to match in the file name or path."},
            "kind": {
                "type": "string",
                "description": "Category filter.",
                "enum": ["document", "spreadsheet", "presentation", "image", "video",
                         "audio", "archive", "code", "data", "font", "other"],
            },
            "ext": {"type": "string", "description": "Extension filter, e.g. 'pdf' or '.png'."},
            "folder": {"type": "string", "description": "Restrict to this folder and below."},
            "modified_within_days": {"type": "integer", "description": "Only files changed in the last N days."},
            "min_size_mb": {"type": "number", "description": "Only files at least this many MB."},
            "sort_by": {
                "type": "string",
                "description": "Ordering of results.",
                "enum": ["newest", "oldest", "largest", "smallest", "name"],
            },
            "limit": {"type": "integer", "description": "Max results, default 25."},
        },
    ),
    _fn(
        "search_content",
        "Full-text search INSIDE indexed files (PDF, Word, Excel, PowerPoint, text, code, "
        "Markdown, CSV). Use when the user asks about the contents of their documents, "
        "e.g. 'which file mentions the Q3 budget'. Returns matching snippets.",
        {
            "query": {"type": "string", "description": "Words to look for inside files."},
            "folder": {"type": "string", "description": "Restrict to this folder and below."},
            "limit": {"type": "integer", "description": "Max results, default 15."},
        },
        ["query"],
    ),
    _fn(
        "read_file",
        "Read the text of one file so you can summarise or answer questions about it. "
        "Always give the full path returned by a previous search.",
        {
            "path": {"type": "string", "description": "Full path to the file."},
            "max_chars": {"type": "integer", "description": "How much text to read, default 4000."},
        },
        ["path"],
    ),
    _fn(
        "list_folder",
        "List the files and subfolders directly inside one folder.",
        {
            "path": {"type": "string", "description": "Full path to the folder."},
            "limit": {"type": "integer", "description": "Max entries, default 50."},
        },
        ["path"],
    ),
    _fn(
        "folder_report",
        "Statistical overview of a folder: file counts and sizes by category and extension, "
        "largest files, how many are over a year old, and the busiest subfolders. "
        "Start here when the user asks to clean up or organise something, or asks what is "
        "taking up space. Omit path to report on everything indexed.",
        {"path": {"type": "string", "description": "Folder to report on. Omit for all indexed folders."}},
    ),
    _fn(
        "find_duplicates",
        "Find files with identical contents, verified by size plus a content hash. "
        "Reports how much space could be reclaimed.",
        {
            "folder": {"type": "string", "description": "Folder to search. Omit for everywhere."},
            "min_size_mb": {"type": "number", "description": "Ignore files smaller than this, default 0.1."},
            "limit": {"type": "integer", "description": "Max duplicate groups, default 15."},
        },
    ),
    _fn(
        "propose_changes",
        "Stage file changes for the user to approve. This does NOT move anything by itself: "
        "the user is shown a table of your plan and clicks through to approve or reject it. "
        "CALLING THIS TOOL IS HOW YOU ASK PERMISSION. Never ask for confirmation in text and "
        "wait — that just stalls, because the approval prompt only appears once you call this. "
        "Use it whenever the user wants files moved, renamed, sorted into folders, or thrown "
        "away. List the real files first with a search or list_folder so every src path exists, "
        "then put every operation in ONE plan, new_folder entries first.",
        {
            "summary": {
                "type": "string",
                "description": "One line describing what this plan does, e.g. 'Sort Downloads into Invoices, Images and Installers'.",
            },
            "operations": {
                "type": "array",
                "description": "The changes to make, in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "move (also covers renaming), new_folder, or trash (moves to macOS Trash, recoverable).",
                            "enum": ["move", "new_folder", "trash"],
                        },
                        "src": {"type": "string", "description": "Full path of the file to move or trash."},
                        "dst": {"type": "string", "description": "Destination path for move, or the folder path for new_folder."},
                    },
                    "required": ["action"],
                },
            },
        },
        ["summary", "operations"],
    ),
    _fn(
        "propose_sort",
        "Stage a plan that sorts every loose file in ONE folder into subfolders. "
        "Prefer this over propose_changes for any 'organise/sort/tidy up this folder' "
        "request — it builds the plan itself, so nothing depends on you retyping filenames. "
        "Like propose_changes it only stages; the user approves or rejects it.",
        {
            "folder": {"type": "string", "description": "The folder to tidy, e.g. '~/Downloads'."},
            "scheme": {
                "type": "string",
                "description": "type = group by category (Documents, Images, Archives…); "
                               "extension = one folder per file extension; "
                               "date = folders named YYYY-MM by last modified date.",
                "enum": ["type", "extension", "date"],
            },
        },
        ["folder", "scheme"],
    ),
]


SYSTEM_PROMPT = """You are mac-chat, a local assistant running entirely on the user's Mac. \
Nothing leaves this machine.

You can see the user's files in these folders only: {roots}
The index currently holds {count} files ({with_text} with searchable text).
Today is {today}.

How to work:
- Use your tools before answering questions about files. Never guess a path or invent a
  filename; every path you mention must have come back from a tool.
- search_files finds files by name/type/date. search_content searches inside them.
  folder_report is the right first move for any "clean up" or "what's taking up space" request.
- To tidy a whole folder, use propose_sort — it builds the plan itself and never mistypes a
  filename. Use propose_changes only for specific, hand-picked moves.
- To change anything on disk, call propose_changes. It only stages a plan; the user is then
  shown every operation and approves or rejects it. You never move files directly, and you
  cannot delete anything permanently — trash goes to the macOS Trash.
- When the user asks you to move, rename, sort or clean up files: list the real files first,
  then CALL propose_changes in the same turn. Do not reply "shall I proceed?" and stop —
  the user only gets an approval prompt once the tool has been called. The tool call is the
  question. After it returns, say in one or two sentences how you grouped things and why.
- Paths shown as ~/... mean the user's home folder. Pass full paths back to tools; ~ is fine.
- Be concise. Short answers, plain language, no filler. Use bullet lists for file listings.
- If a tool returns nothing useful, say so plainly rather than inventing an answer."""
