"""mac-chat: a fully local chatbot for searching and organising files on your Mac."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import config, db, indexer, ollama, schemas, tools

console = Console()
MAX_TOOL_ROUNDS = 10
HISTORY_TURNS = 24


def rule(text: str) -> None:
    console.rule(f"[dim]{text}[/dim]", style="dim")


def index_counts() -> tuple[int, int]:
    conn = db.connect()
    row = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(has_text),0) t FROM files"
    ).fetchone()
    conn.close()
    return row["c"], row["t"]


def run_index(cfg: dict, full: bool = False) -> None:
    label = "Rebuilding" if full else "Updating"
    with console.status(f"[cyan]{label} index…", spinner="dots") as status:
        def progress(st):
            status.update(
                f"[cyan]{label} index… {st['scanned']:,} scanned, "
                f"{st['added'] + st['updated']:,} indexed, {st['text']:,} with text"
            )
        started = time.time()
        st = indexer.build(cfg, full=full, progress=progress)
    console.print(
        f"[green]Index ready[/green] — {st['scanned']:,} files scanned, "
        f"{st['added']:,} added, {st['updated']:,} updated, {st['text']:,} with searchable text, "
        f"{st['removed']:,} stale entries dropped ({time.time() - started:.0f}s)"
    )


def show_plan(plan: tools.Plan) -> None:
    table = Table(
        title=f"Proposed changes — {plan.summary}",
        title_style="bold yellow",
        header_style="bold",
        show_lines=False,
        expand=True,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Action", width=11)
    table.add_column("From", overflow="fold")
    table.add_column("To", overflow="fold")
    colours = {"move": "cyan", "trash": "red", "new_folder": "green"}
    for i, op in enumerate(plan.operations, 1):
        table.add_row(
            str(i),
            f"[{colours.get(op['action'], 'white')}]{op['action']}[/]",
            tools.short(op["src"]) if op["src"] else "—",
            tools.short(op["dst"]) if op["dst"] else "macOS Trash",
        )
    console.print(table)


def confirm_plan(session: tools.Session) -> None:
    plan = session.pending
    if not plan:
        return
    session.pending = None
    show_plan(plan)
    n = len(plan.operations)
    console.print(
        f"[yellow]Apply these {n} change(s)?[/yellow] "
        "[dim]y = yes,  n = no,  s = show numbers to skip[/dim]"
    )
    try:
        answer = console.input("[bold yellow]apply?[/bold yellow] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]Cancelled — nothing changed.[/dim]")
        return

    if answer.startswith("s"):
        raw = console.input("[yellow]Numbers to SKIP (e.g. 2,5-7):[/yellow] ").strip()
        skip: set[int] = set()
        for part in raw.replace(" ", "").split(","):
            if "-" in part:
                try:
                    a, b = part.split("-")
                    skip.update(range(int(a), int(b) + 1))
                except ValueError:
                    continue
            elif part.isdigit():
                skip.add(int(part))
        plan.operations = [op for i, op in enumerate(plan.operations, 1) if i not in skip]
        if not plan.operations:
            console.print("[dim]Everything skipped — nothing changed.[/dim]")
            return
        console.print(f"[dim]{len(plan.operations)} operation(s) remain.[/dim]")
        answer = console.input("[bold yellow]apply?[/bold yellow] ").strip().lower()

    if not answer.startswith("y"):
        console.print("[dim]Rejected — nothing changed.[/dim]")
        return

    done, errors = session.apply(plan)
    console.print(f"[green]Applied {done} of {len(plan.operations)} operation(s).[/green]")
    if errors:
        console.print("[red]Problems:[/red]\n  " + "\n  ".join(errors))
    console.print("[dim]Use /undo to reverse this batch.[/dim]")


def call_tool(session: tools.Session, name: str, args: dict) -> str:
    fn = {
        "search_files": session.search_files,
        "search_content": session.search_content,
        "read_file": session.read_file,
        "list_folder": session.list_folder,
        "folder_report": session.folder_report,
        "find_duplicates": session.find_duplicates,
        "propose_changes": session.propose_changes,
        "propose_sort": session.propose_sort,
    }.get(name)
    if not fn:
        return f"Error: no tool named {name!r}."
    try:
        return fn(**{k: v for k, v in args.items() if v not in (None, "")})
    except TypeError as exc:
        return f"Error: bad arguments for {name}: {exc}"
    except Exception as exc:
        return f"Error running {name}: {exc}"


def parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def turn(session: tools.Session, messages: list[dict], think: bool) -> None:
    cfg = session.cfg
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            with console.status("[dim]thinking…[/dim]", spinner="dots"):
                msg = ollama.chat(
                    cfg["ollama_url"], cfg["model"], messages,
                    tools=schemas.TOOLS, num_ctx=cfg["num_ctx"], think=think,
                )
        except ollama.OllamaError as exc:
            console.print(f"[red]{exc}[/red]")
            return

        calls = msg.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": msg.get("content", ""),
            **({"tool_calls": calls} if calls else {}),
        })

        if not calls:
            content = strip_think(msg.get("content", ""))
            console.print()
            console.print(Markdown(content) if content else "[dim](no reply)[/dim]")
            console.print()
            return

        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = parse_args(fn.get("arguments"))
            shown = ", ".join(f"{k}={v!r}" for k, v in args.items() if v not in (None, ""))
            console.print(f"[dim]→ {name}({shown[:150]})[/dim]")
            result = call_tool(session, name, args)
            messages.append({"role": "tool", "tool_name": name, "name": name, "content": result})

    console.print("[yellow]Stopped after 10 tool calls — ask me something narrower.[/yellow]")


HELP = """
[bold]Commands[/bold]
  /index          update the index (fast, only changed files)
  /reindex        rebuild the index from scratch
  /stats          what is in the index right now
  /undo           reverse the last batch of applied changes
  /think on|off   let the model reason before answering (slower, better on hard tasks)
  /model [name]   show or switch the Ollama model
  /roots          which folders mac-chat can see
  /clear          forget the conversation so far
  /help           this list
  /quit           exit

[bold]Try asking[/bold]
  what is taking up the most space in Downloads?
  find every PDF I touched in the last month
  which of my files mention "lakehouse"?
  summarise ~/Documents/notes.md
  sort my Downloads folder into subfolders by type
  find duplicate files in Documents
"""


def repl(cfg: dict, think: bool) -> None:
    session = tools.Session(cfg)
    count, with_text = index_counts()
    system = schemas.SYSTEM_PROMPT.format(
        roots=", ".join(tools.short(r) for r in cfg["roots"]),
        count=f"{count:,}", with_text=f"{with_text:,}", today=date.today().isoformat(),
    )
    messages: list[dict] = [{"role": "system", "content": system}]

    console.print(Panel.fit(
        f"[bold]mac-chat[/bold]  [dim]·[/dim]  {cfg['model']}  [dim]·[/dim]  "
        f"{count:,} files indexed  [dim]·[/dim]  [green]fully offline[/green]\n"
        f"[dim]{', '.join(tools.short(r) for r in cfg['roots'])}[/dim]\n"
        "[dim]/help for commands · Ctrl-C to interrupt · /quit to exit[/dim]",
        border_style="cyan",
    ))

    while True:
        try:
            user = console.input("\n[bold cyan]you ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            break
        if not user:
            continue

        if user.startswith("/"):
            cmd, _, rest = user[1:].partition(" ")
            cmd, rest = cmd.lower(), rest.strip()
            if cmd in ("quit", "exit", "q"):
                break
            if cmd == "help":
                console.print(HELP)
            elif cmd == "index":
                run_index(cfg, full=False)
            elif cmd == "reindex":
                run_index(cfg, full=True)
            elif cmd == "stats":
                console.print(session.folder_report())
            elif cmd == "undo":
                console.print(session.undo_last())
            elif cmd == "clear":
                del messages[1:]
                console.print("[dim]Conversation cleared.[/dim]")
            elif cmd == "roots":
                for r in cfg["roots"]:
                    console.print(f"  {tools.short(r)}")
            elif cmd == "think":
                if rest in ("on", "off"):
                    think = rest == "on"
                console.print(f"[dim]thinking is {'on' if think else 'off'}[/dim]")
            elif cmd == "model":
                if rest:
                    cfg["model"] = rest
                    config.save(cfg)
                    console.print(f"[dim]model set to {rest}[/dim]")
                else:
                    console.print(f"[dim]current: {cfg['model']}[/dim]")
                    for m in ollama.models(cfg["ollama_url"]):
                        console.print(f"  {m}")
            else:
                console.print(f"[yellow]Unknown command /{cmd} — try /help[/yellow]")
            continue

        messages.append({"role": "user", "content": user})
        if len(messages) > HISTORY_TURNS * 2:
            messages[:] = messages[:1] + messages[-HISTORY_TURNS * 2:]
        try:
            turn(session, messages, think)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            continue
        confirm_plan(session)

    session.close()


def main() -> int:
    ap = argparse.ArgumentParser(prog="mac-chat", description="Local AI chat over your Mac's files.")
    ap.add_argument("--index", action="store_true", help="update the index and exit")
    ap.add_argument("--reindex", action="store_true", help="rebuild the index from scratch and exit")
    ap.add_argument("--model", help="Ollama model to use")
    ap.add_argument("--think", action="store_true", help="start with model reasoning enabled")
    ap.add_argument("--ask", help="ask one question, print the answer, exit")
    args = ap.parse_args()

    cfg = config.load()
    if args.model:
        cfg["model"] = args.model

    if not ollama.available(cfg["ollama_url"]):
        console.print("[red]Ollama is not running.[/red] Start it with:\n"
                      "  [bold]brew services start ollama[/bold]")
        return 1
    installed = ollama.models(cfg["ollama_url"])
    if installed and not any(m.split(":")[0] == cfg["model"].split(":")[0] for m in installed):
        console.print(f"[red]Model {cfg['model']} is not installed.[/red] Pull it with:\n"
                      f"  [bold]ollama pull {cfg['model']}[/bold]\n"
                      f"Installed: {', '.join(installed) or 'none'}")
        return 1

    if args.reindex or args.index:
        run_index(cfg, full=args.reindex)
        return 0

    count, _ = index_counts()
    if count == 0:
        console.print("[yellow]No index yet — building it now. "
                      "This takes a few minutes the first time.[/yellow]")
        run_index(cfg, full=True)

    if args.ask:
        session = tools.Session(cfg)
        c, t = index_counts()
        system = schemas.SYSTEM_PROMPT.format(
            roots=", ".join(tools.short(r) for r in cfg["roots"]),
            count=f"{c:,}", with_text=f"{t:,}", today=date.today().isoformat(),
        )
        turn(session, [{"role": "system", "content": system},
                       {"role": "user", "content": args.ask}], args.think)
        if session.pending:
            confirm_plan(session)
        session.close()
        return 0

    repl(cfg, args.think)
    return 0


if __name__ == "__main__":
    sys.exit(main())
