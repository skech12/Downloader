import argparse
import hashlib
import sys
import time

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

import download as dt

console = Console()


def rag_post(query: str, verbose: bool = False) -> dict:
    headers: dict = {}
    if dt._session_token:
        headers["X-Session-Token"] = dt._session_token
    elif dt._bearer_token:
        headers["Authorization"] = f"Bearer {dt._bearer_token}"
    t0 = time.time()
    resp = requests.post(
        f"{dt.API_BASE_URL}/api/tui/rag/query",
        headers=headers,
        json={"query": query},
        timeout=(10, 120),
    )
    elapsed = time.time() - t0
    qh = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    if verbose:
        console.print(f"[dim]query_sha12={qh} latency={elapsed:.2f}s[/dim]")
    if resp.status_code == 429:
        raise dt.APILimitError(resp.json().get("detail", "Rate limited"))
    if resp.status_code == 401:
        raise dt.APILimitError(resp.json().get("detail", "Unauthorized"))
    resp.raise_for_status()
    return resp.json()


def print_results(data: dict, verbose: bool):
    if data.get("status") == "no_match":
        console.print(Panel(data.get("message", "No match."), title="RAG", border_style="yellow"))
        return
    rows = data.get("results") or []
    if not rows:
        console.print("[yellow]No results.[/yellow]")
        return
    table = Table(title="Search results", show_header=True)
    table.add_column("#", justify="right")
    table.add_column("Key", max_width=48)
    table.add_column("Snippet", max_width=70)
    for i, r in enumerate(rows, 1):
        table.add_row(str(i), r.get("key", ""), r.get("snippet", ""))
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="RAG search against datasite (TUI session).")
    parser.add_argument("-q", "--query", default=None, help="Search query")
    parser.add_argument("--verbose", action="store_true", help="Log query hash + timing (no raw query)")
    parser.add_argument("--logout", action="store_true", help="Clear stored JWT (same file as downloadTUI)")
    parser.add_argument("--login", action="store_true", help="Force GitHub login (same as downloadTUI)")
    args = parser.parse_args()

    if args.logout:
        dt.clear_token()
        console.print("[green]Logged out.[/green]")
        return

    if args.login:
        dt.clear_token()
        token = dt.github_login_flow()
        if not token:
            sys.exit(1)
        dt.save_token(token)
        dt._bearer_token = token
        console.print("[green]Login successful.[/green]")

    if not dt.ensure_authenticated():
        console.print("[red]Authentication failed.[/red]")
        sys.exit(1)
    if not dt.acquire_session():
        sys.exit(1)

    q = (args.query or "").strip()
    if not q:
        q = Prompt.ask("[bold cyan]Search query[/bold cyan]").strip()
    if not q:
        console.print("[yellow]Empty query.[/yellow]")
        sys.exit(1)

    try:
        data = rag_post(q, verbose=args.verbose)
        print_results(data, args.verbose)
    except dt.APILimitError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
