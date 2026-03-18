import os
import sys
import time
import json
import re
import warnings
import shutil
import subprocess
import argparse
import http.server
import socketserver
import threading
import webbrowser
from urllib.parse import quote as _url_quote

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* doesn't match a supported version!",
    category=Warning,
    module=r"requests(\..*)?$",
)

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress, BarColumn, TextColumn, DownloadColumn,
    TransferSpeedColumn, TimeRemainingColumn,
)
from rich.prompt import Prompt, Confirm
from rich import box

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

console = Console()

API_BASE_URL = os.getenv("DATASITE_API_URL", "https://neurvance-bb82540cb249.herokuapp.com")
APP_VERSION = "1.0.0"
GITHUB_REPO = "skech12/Neurvance"
GITHUB_BRANCH = "main"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".datasite_history.json")
DEFAULT_WORKERS = 4
STREAM_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 ".stream_state.json")
SEMVER_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")

_bearer_token = ""
_session_token = ""

TOKEN_FILE = Path.home() / ".datasite" / "config.json"


class APILimitError(Exception):
    pass


# ── Token storage ─────────────────────────────────────────────────────────────

def load_token() -> str:
    try:
        if TOKEN_FILE.exists():
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("token", "")
    except (json.JSONDecodeError, OSError):
        pass
    return ""


def save_token(token: str):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"token": token}), encoding="utf-8")


def clear_token():
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def github_login_flow() -> str:
    """Open browser for GitHub OAuth, return JWT token on success."""
    result = {"token": None}
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            token = params.get("token", [None])[0]
            if token:
                result["token"] = token
                html = (b"<html><body style='font-family:sans-serif;margin:40px'>"
                        b"<h2>Login successful!</h2><p>You can close this tab.</p></body></html>")
            else:
                import html as _html
                error = _html.escape(params.get("error", ["Unknown error"])[0])
                html = (f"<html><body style='font-family:sans-serif;margin:40px'>"
                        f"<h2>Login failed</h2><p>{error}</p></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            done.set()

        def log_message(self, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as server:
        port = server.server_address[1]
        callback_url = f"http://127.0.0.1:{port}/callback"
        login_url = f"{API_BASE_URL}/auth/github/login?tui_redirect={_url_quote(callback_url)}"
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()
        console.print("\n[bold cyan]Opening browser for GitHub login...[/bold cyan]")
        console.print(f"[dim]If your browser doesn't open, visit:[/dim] {login_url}\n")
        webbrowser.open(login_url)
        if not done.wait(timeout=300):
            console.print("[red]Login timed out (5 min). Run the tool again to retry.[/red]")
            return ""
    return result["token"] or ""


def ensure_authenticated() -> bool:
    """Load stored token or trigger GitHub login. Returns True if authenticated."""
    global _bearer_token
    _bearer_token = load_token()
    if _bearer_token:
        return True
    console.print("[yellow]No stored credentials. Starting GitHub login...[/yellow]")
    token = github_login_flow()
    if not token:
        return False
    save_token(token)
    _bearer_token = token
    console.print("[green]Login successful![/green]")
    return True


# ── API Client ────────────────────────────────────────────────────────────────

def api_get(path, **params):
    try:
        headers = {}
        if _session_token:
            headers["X-Session-Token"] = _session_token
        elif _bearer_token:
            headers["Authorization"] = f"Bearer {_bearer_token}"
        resp = requests.get(
            f"{API_BASE_URL}{path}",
            params={k: v for k, v in params.items() if v is not None},
            headers=headers,
            timeout=(5, 120),  # 5s connect, 120s read
        )
    except requests.exceptions.ConnectionError:
        console.print(f"\n[red]Cannot connect to server at {API_BASE_URL}[/red]")
        raise SystemExit(1)
    except requests.exceptions.Timeout:
        console.print("\n[red]Request timed out.[/red]")
        raise SystemExit(1)
    if resp.status_code == 403:
        detail = resp.json().get("detail", "No API calls remaining.")
        raise APILimitError(detail)
    if resp.status_code == 401:
        detail = resp.json().get("detail", "Session expired.")
        raise APILimitError(detail)
    resp.raise_for_status()
    return resp.json()


# ── Session management ────────────────────────────────────────────────────

def acquire_session() -> bool:
    """Create a download session (charges 1 API call). Returns True on success."""
    global _session_token, _bearer_token
    if _session_token:
        return True
    if not _bearer_token:
        console.print("[red]Not authenticated. Run the tool with --login.[/red]")
        return False
    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/tui/session",
            headers={"Authorization": f"Bearer {_bearer_token}"},
            timeout=(5, 30),
        )
    except requests.exceptions.ConnectionError:
        console.print(f"\n[red]Cannot connect to server at {API_BASE_URL}[/red]")
        return False
    except requests.exceptions.Timeout:
        console.print("\n[red]Session request timed out.[/red]")
        return False
    if resp.status_code == 401:
        console.print("[yellow]Token expired. Re-authenticating...[/yellow]")
        clear_token()
        _bearer_token = ""
        if not ensure_authenticated():
            return False
        return acquire_session()
    if resp.status_code == 403:
        detail = resp.json().get("detail", "No API calls remaining.")
        console.print(f"[red]{detail}[/red]")
        return False
    resp.raise_for_status()
    data = resp.json()
    _session_token = data["session_token"]
    console.print(f"[green]Session started (expires {data['expires_at'][:16]}) — "
                  f"1 API call charged, {data['calls_remaining']} remaining[/green]")
    return True


def load_stream_state():
    if os.path.exists(STREAM_STATE_FILE):
        try:
            with open(STREAM_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_stream_state(state):
    with open(STREAM_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def clear_stream_state():
    if os.path.exists(STREAM_STATE_FILE):
        os.remove(STREAM_STATE_FILE)


# ── Helpers ──────────────────────────────────────────────────────────────────

def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def sanitize_filename(filename):
    for ch in '<>:"|?*':
        filename = filename.replace(ch, "_")
    return filename


def parse_selection(raw, max_val):
    """Parse '1,3,5' or '1-5' or 'all' into a sorted list of ints."""
    raw = raw.strip().lower()
    if raw in ("all", "0", "*"):
        return list(range(1, max_val + 1))
    selected = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                selected.update(range(int(lo), int(hi) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            selected.add(int(part))
    return sorted(n for n in selected if 1 <= n <= max_val)


def _semver_tuple(version):
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0, 0, 0)


def fetch_github_versions_from_filenames(repo=GITHUB_REPO, branch=GITHUB_BRANCH):
    """Return semantic versions found in GitHub filenames or version file contents."""
    repo_url = f"https://api.github.com/repos/{repo}"
    repo_resp = requests.get(
        repo_url,
        headers={"Accept": "application/vnd.github+json"},
        timeout=(5, 15),
    )
    repo_resp.raise_for_status()
    default_branch = repo_resp.json().get("default_branch") or branch

    url = f"https://api.github.com/repos/{repo}/git/trees/{default_branch}?recursive=1"
    resp = requests.get(
        url,
        headers={"Accept": "application/vnd.github+json"},
        timeout=(5, 15),
    )
    if resp.status_code == 409:
        return set(), default_branch, "empty"
    resp.raise_for_status()
    tree = resp.json().get("tree", [])

    versions = set()
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path = entry.get("path", "")
        filename = os.path.basename(path)
        lower_name = filename.lower()
        if "version" not in lower_name:
            continue
        # Try to find semver in the filename itself
        found = SEMVER_RE.findall(filename)
        if found:
            versions.update(found)
        else:
            # Filename is just "version" (no number) — read the file contents
            raw_url = (
                f"https://raw.githubusercontent.com/{repo}/"
                f"{default_branch}/{path}"
            )
            try:
                raw_resp = requests.get(raw_url, timeout=(5, 10))
                if raw_resp.status_code == 200:
                    versions.update(SEMVER_RE.findall(raw_resp.text))
            except requests.RequestException:
                pass

    return versions, default_branch, None


def check_github_version():
    """Check GitHub filenames for version and compare against APP_VERSION."""
    try:
        versions, checked_branch, state = fetch_github_versions_from_filenames()
    except requests.RequestException as e:
        console.print(f"[dim]Version check skipped (GitHub unavailable: {e}).[/dim]")
        return

    if state == "empty":
        console.print(
            f"[dim]Version check skipped (GitHub repo has no committed files on '{checked_branch}' yet).[/dim]"
        )
        return

    if not versions:
        console.print(
            f"[dim]Version check: no filename with 'version' + X.Y.Z found on GitHub branch '{checked_branch}'.[/dim]"
        )
        return

    latest = max(versions, key=_semver_tuple)
    if APP_VERSION in versions:
        console.print(f"[green]Version check: running {APP_VERSION} (found on GitHub).[/green]")
        return

    if _semver_tuple(latest) > _semver_tuple(APP_VERSION):
        console.print(
            f"[bold yellow]Update available:[/bold yellow] local {APP_VERSION}  →  GitHub {latest}"
        )
    else:
        console.print(
            f"[yellow]Version check: local {APP_VERSION} not found in GitHub version filenames; latest found {latest}.[/yellow]"
        )


# ── History ──────────────────────────────────────────────────────────────────

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"downloads": []}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def get_downloaded_keys():
    history = load_history()
    return {d["key"] for d in history.get("downloads", [])}


def show_history():
    history = load_history()
    entries = history.get("downloads", [])
    if not entries:
        console.print("[yellow]No download history yet.[/yellow]")
        return
    table = Table(
        title="Download History", box=box.ROUNDED,
        title_style="bold cyan", padding=(0, 1),
    )
    table.add_column("#", style="yellow", width=4, justify="right")
    table.add_column("Filename", style="bold", max_width=40)
    table.add_column("Size", style="green", justify="right", width=9)
    table.add_column("Date", style="dim", width=19)
    recent = entries[-20:]
    for i, entry in enumerate(recent, len(entries) - len(recent) + 1):
        fname = entry["key"].split("/")[-1] if "/" in entry["key"] else entry["key"]
        table.add_row(str(i), fname, fmt_size(entry.get("size", 0)),
                      entry.get("timestamp", ""))
    console.print(table)
    console.print(f"[dim]{len(entries)} total downloads[/dim]")


# ── Display ──────────────────────────────────────────────────────────────────

def show_categories(categories, file_counts):
    downloaded_keys = get_downloaded_keys()
    table = Table(
        title="Categories", box=box.ROUNDED, title_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("#", style="bold yellow", width=4, justify="right")
    table.add_column("Category", style="bold white")
    table.add_column("Files", style="green", justify="right", width=6)
    table.add_column("", width=2)
    for i, cat in enumerate(categories, 1):
        has_dl = any(f"/{cat}/" in k for k in downloaded_keys)
        indicator = "[green]●[/green]" if has_dl else ""
        table.add_row(str(i), cat, str(file_counts.get(cat, 0)), indicator)
    console.print(table)


def show_search_results(results, page=1, page_size=15):
    total = len(results)
    pages = max(1, -(-total // page_size))
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    chunk = results[start:start + page_size]

    table = Table(
        title=f"Results  ({total} found)  Page {page}/{pages}",
        box=box.ROUNDED, title_style="bold green", padding=(0, 1),
    )
    table.add_column("#", style="yellow", width=5, justify="right")
    table.add_column("Filename", style="bold", max_width=42, no_wrap=True)
    table.add_column("Category", max_width=22, no_wrap=True)
    table.add_column("Uploader", max_width=18, no_wrap=True)
    table.add_column("Size", style="green", justify="right", width=9)
    for i, r in enumerate(chunk, start + 1):
        table.add_row(str(i), r["filename"], r["category"],
                      r["uploader"], fmt_size(r["size"]))
    console.print(table)
    return pages


def show_file_listing(files, category, page=1, page_size=20):
    """Show paginated file listing for a category."""
    total = len(files)
    pages = max(1, -(-total // page_size))
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    chunk = files[start:start + page_size]

    table = Table(
        title=f"{category}  ({total} files)  Page {page}/{pages}",
        box=box.ROUNDED, title_style="bold magenta", padding=(0, 1),
    )
    table.add_column("#", style="yellow", width=5, justify="right")
    table.add_column("Filename", style="bold", max_width=44, no_wrap=True)
    table.add_column("Uploader", max_width=18, no_wrap=True)
    table.add_column("Size", style="green", justify="right", width=9)
    for i, item in enumerate(chunk, start + 1):
        table.add_row(str(i), item["filename"], item["uploader"],
                      fmt_size(item["size"]))
    console.print(table)
    return pages


# ── File Preview ─────────────────────────────────────────────────────────────

def preview_file(key):
    """Fetch first 25 lines of a file via the API and display."""
    try:
        data = api_get("/api/tui/preview", key=key)
        preview_text = data["content"]
        if data.get("truncated"):
            preview_text += "\n[dim]… (truncated)[/dim]"
        console.print(Panel(preview_text, title=f"Preview: {data['filename']}",
                            border_style="blue", expand=False))
    except Exception as e:
        console.print(f"[red]Preview failed: {e}[/red]")


# ── Download (parallel) ──────────────────────────────────────────────────────

def _download_single(item, output_dir, prog, overall_task):
    """Download a single file via presigned URL. Called from thread pool or sequentially."""
    key = item["key"] if isinstance(item, dict) else item
    expected = item.get("size", 0) if isinstance(item, dict) else 0
    parts = key.split("/")
    parts[-1] = sanitize_filename(parts[-1])
    rel = os.path.join(*parts[2:])
    local = os.path.normpath(os.path.join(output_dir, rel))
    if not local.startswith(os.path.normpath(output_dir) + os.sep):
        raise ValueError(f"Path traversal blocked: {key}")
    os.makedirs(os.path.dirname(local), exist_ok=True)

    # Resume — skip files that already match expected size
    if expected and os.path.exists(local) and os.path.getsize(local) == expected:
        prog.advance(overall_task)
        return {"status": "skipped", "key": key, "size": 0, "path": local}

    # Get presigned URL from API
    url_data = api_get("/api/tui/download-url", key=key)
    download_url = url_data["url"]
    actual_expected = url_data.get("size") or expected

    label = parts[-1][:38] + ("…" if len(parts[-1]) > 38 else "")
    ftask = prog.add_task(f"  {label}", total=actual_expected or None)

    transferred = 0
    with requests.get(download_url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(local, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                transferred += len(chunk)
                prog.update(ftask, completed=transferred)

    if not actual_expected:
        prog.update(ftask, completed=1, total=1)
    actual_size = actual_expected or os.path.getsize(local)
    prog.advance(overall_task)
    return {"status": "downloaded", "key": key, "size": actual_size, "path": local}


def download_keys(keys, output_dir="downloads", workers=DEFAULT_WORKERS,
                  skip_confirm=False):
    if not keys:
        console.print("[yellow]Nothing to download.[/yellow]")
        return

    total_size = sum(k.get("size", 0) if isinstance(k, dict) else 0 for k in keys)
    console.print(
        f"\n[bold]About to download [cyan]{len(keys)}[/cyan] file(s) "
        f"([cyan]{fmt_size(total_size)}[/cyan])[/bold]"
    )
    if not skip_confirm and not Confirm.ask("[bold]Continue?", default=True):
        console.print("[dim]Cancelled.[/dim]")
        return

    os.makedirs(output_dir, exist_ok=True)
    downloaded, skipped, total_bytes = 0, 0, 0
    completed_downloads = []
    t0 = time.time()
    interrupted = False

    try:
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=36),
            "[progress.percentage]{task.percentage:>3.0f}%",
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console, expand=False,
        ) as prog:
            overall = prog.add_task("Overall", total=len(keys))
            w = min(workers, len(keys))

            if w > 1:
                with ThreadPoolExecutor(max_workers=w) as pool:
                    futures = {
                        pool.submit(_download_single, item, output_dir,
                                    prog, overall): item
                        for item in keys
                    }
                    for future in as_completed(futures):
                        try:
                            result = future.result()
                        except Exception as e:
                            console.print(f"[red]Error: {e}[/red]")
                            prog.advance(overall)
                            continue
                        if result["status"] == "skipped":
                            skipped += 1
                        else:
                            downloaded += 1
                            total_bytes += result["size"]
                            completed_downloads.append(result)
            else:
                for item in keys:
                    try:
                        result = _download_single(item, output_dir, prog, overall)
                    except Exception as e:
                        console.print(f"[red]Error: {e}[/red]")
                        prog.advance(overall)
                        continue
                    if result["status"] == "skipped":
                        skipped += 1
                    else:
                        downloaded += 1
                        total_bytes += result["size"]
                        completed_downloads.append(result)
    except KeyboardInterrupt:
        interrupted = True

    # Save history
    if completed_downloads:
        history = load_history()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        for r in completed_downloads:
            history["downloads"].append({
                "key": r["key"], "size": r["size"],
                "path": r["path"], "timestamp": ts,
            })
        history["downloads"] = history["downloads"][-500:]
        save_history(history)

    # Summary
    elapsed = time.time() - t0
    title = "Download Interrupted" if interrupted else "Download Complete"
    style = "bold yellow" if interrupted else "bold green"
    tbl = Table(box=box.HEAVY, show_header=False, title=title,
                title_style=style, padding=(0, 2))
    tbl.add_column("", style="bold")
    tbl.add_column("")
    tbl.add_row("Downloaded", f"{downloaded} file(s)")
    if skipped:
        tbl.add_row("Skipped (exists)", f"{skipped} file(s)")
    tbl.add_row("Total size", fmt_size(total_bytes))
    tbl.add_row("Time", f"{elapsed:.1f}s")
    tbl.add_row("Saved to", os.path.abspath(output_dir))
    console.print()
    console.print(tbl)


# ── Streaming batch download (gradual mode) ─────────────────────────────

def _check_disk_space(output_dir, warn_mb=500):
    """Check free disk space and warn if low. Returns free bytes."""
    try:
        usage = shutil.disk_usage(output_dir if os.path.exists(output_dir) else os.path.dirname(output_dir))
        free_mb = usage.free / (1024 * 1024)
        if free_mb < warn_mb:
            console.print(f"[bold yellow]Warning: Only {free_mb:.0f} MB free disk space![/yellow]")
        return usage.free
    except OSError:
        return 0


def download_batch_streaming(
    output_dir="downloads",
    workers=DEFAULT_WORKERS,
    batch_size=10,
    category=None,
    cleanup=True,
    callback=None,
    skip_confirm=False,
):
    """Download all files in batches of `batch_size`. After each batch, optionally
    run a callback command, then delete the local files before downloading the next batch.
    Uses a session token so only 1 API call is charged for the entire run."""

    # Acquire session (single API call charge)
    if not _session_token:
        if not acquire_session():
            return

    # Get total count
    with console.status("[bold cyan]Getting file count…"):
        count_data = api_get("/api/tui/files/count", category=category)
    total_files = count_data["total"]
    if total_files == 0:
        console.print("[yellow]No files found.[/yellow]")
        return

    total_batches = -(-total_files // batch_size)  # ceil division

    # Check for resume state
    state = load_stream_state()
    start_offset = 0
    if state.get("category") == (category or "__all__") and state.get("batch_size") == batch_size:
        start_offset = state.get("completed_offset", 0)
        if start_offset > 0 and start_offset < total_files:
            start_batch = start_offset // batch_size + 1
            if Confirm.ask(
                f"[yellow]Resume from batch {start_batch}/{total_batches} "
                f"(offset {start_offset})?[/yellow]", default=True
            ):
                console.print(f"[green]Resuming from offset {start_offset}[/green]")
            else:
                start_offset = 0
        else:
            start_offset = 0

    # Check disk space
    _check_disk_space(output_dir)

    console.print(Panel(
        f"[bold]Streaming batch download[/bold]\n"
        f"Files: [cyan]{total_files}[/cyan]  "
        f"Batch size: [cyan]{batch_size}[/cyan]  "
        f"Batches: [cyan]{total_batches}[/cyan]  "
        f"Cleanup: [cyan]{'yes' if cleanup else 'no'}[/cyan]"
        + (f"\nCallback: [dim]{callback}[/dim]" if callback else ""),
        title="Gradual Download", border_style="green",
    ))

    if not skip_confirm and not Confirm.ask("[bold]Start?", default=True):
        console.print("[dim]Cancelled.[/dim]")
        return

    t0 = time.time()
    total_downloaded = 0
    total_bytes = 0
    offset = start_offset
    batch_num = start_offset // batch_size + 1
    interrupted = False

    try:
        while offset < total_files:
            console.print(f"\n[bold cyan]─── Batch {batch_num}/{total_batches} "
                          f"(files {offset+1}–{min(offset+batch_size, total_files)}"
                          f" of {total_files}) ───[/bold cyan]")

            # Fetch this batch's file list from the paginated endpoint
            page_data = api_get("/api/tui/files/page",
                                category=category, offset=offset, limit=batch_size)
            files = page_data["files"]

            if not files:
                break

            # Build download items
            keys = [{"key": f["key"], "size": f["size"]} for f in files]

            # Download this batch
            batch_dir = os.path.join(output_dir, f"_batch_{batch_num}")
            os.makedirs(batch_dir, exist_ok=True)

            batch_downloaded = 0
            batch_bytes = 0

            with Progress(
                TextColumn("[bold blue]{task.description}"),
                BarColumn(bar_width=36),
                "[progress.percentage]{task.percentage:>3.0f}%",
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console, expand=False,
            ) as prog:
                overall = prog.add_task(f"Batch {batch_num}", total=len(keys))
                for item in keys:
                    try:
                        result = _download_single(item, batch_dir, prog, overall)
                    except Exception as e:
                        console.print(f"[red]Error: {e}[/red]")
                        prog.advance(overall)
                        continue
                    if result["status"] != "skipped":
                        batch_downloaded += 1
                        batch_bytes += result["size"]

            total_downloaded += batch_downloaded
            total_bytes += batch_bytes

            # Run callback if provided
            if callback:
                console.print(f"[dim]Running callback: {callback}[/dim]")
                try:
                    subprocess.run(callback, shell=True, check=True,
                                   cwd=batch_dir)
                    console.print("[green]Callback completed.[/green]")
                except subprocess.CalledProcessError as e:
                    console.print(f"[red]Callback failed (exit {e.returncode})[/red]")

            # Cleanup batch files
            if cleanup:
                shutil.rmtree(batch_dir, ignore_errors=True)
                console.print(f"[dim]Batch {batch_num} cleaned up.[/dim]")

            # Save resume state
            offset += len(files)
            batch_num += 1
            save_stream_state({
                "category": category or "__all__",
                "batch_size": batch_size,
                "completed_offset": offset,
                "session_token": _session_token,
            })

    except KeyboardInterrupt:
        interrupted = True
        console.print("\n[yellow]Interrupted. Progress saved — resume with same command.[/yellow]")

    # Clear state on completion
    if not interrupted:
        clear_stream_state()

    # Summary
    elapsed = time.time() - t0
    title = "Streaming Download Interrupted" if interrupted else "Streaming Download Complete"
    style = "bold yellow" if interrupted else "bold green"
    tbl = Table(box=box.HEAVY, show_header=False, title=title,
                title_style=style, padding=(0, 2))
    tbl.add_column("", style="bold")
    tbl.add_column("")
    tbl.add_row("Downloaded", f"{total_downloaded} file(s)")
    tbl.add_row("Total size", fmt_size(total_bytes))
    tbl.add_row("Batches", f"{batch_num - (start_offset // batch_size + 1)}"
                            f" of {total_batches}")
    tbl.add_row("Time", f"{elapsed:.1f}s")
    tbl.add_row("Cleanup", "enabled" if cleanup else "disabled")
    tbl.add_row("API calls used", "1 (session)")
    console.print()
    console.print(tbl)


# ── Search ───────────────────────────────────────────────────────────────────

def search(query, all_files):
    q = query.lower()
    hits = []
    for cat, files in all_files.items():
        for f in files:
            uploader = f[0] if f else ""
            filename = f[1] if len(f) > 1 else ""
            key = f[2] if len(f) > 2 else ""
            size = f[3] if len(f) > 3 else 0
            if q in filename.lower() or q in uploader.lower() or q in cat.lower():
                hits.append({"category": cat, "uploader": uploader,
                             "filename": filename, "key": key, "size": size})
    return hits


# ── File listing sub-loop ────────────────────────────────────────────────────

def browse_category(category, all_files, output_dir, workers, skip_confirm):
    """Interactive file browser for a single category."""
    raw = all_files.get(category, [])
    if not raw:
        console.print(f"[yellow]No files in {category}.[/yellow]")
        return

    files = [
        {
            "uploader": f[0],
            "filename": f[1] if len(f) > 1 else "",
            "key": f[2] if len(f) > 2 else "",
            "size": f[3] if len(f) > 3 else 0,
        }
        for f in raw
    ]

    page = 1
    while True:
        pages = show_file_listing(files, category, page)
        console.print(
            "\n[bold]d 1,3[/bold]=download  [bold]d all[/bold]=all  "
            "[bold]p N[/bold]=preview  "
            "[bold]n[/bold]=next  [bold]p[/bold]=prev  [bold]b[/bold]=back"
        )
        act = Prompt.ask("[bold cyan]›").strip().lower()

        if act in ("n", "next") and page < pages:
            page += 1
        elif len(act) > 1 and act[0] == "p" and act[1:].strip().isdigit():
            idx = int(act[1:].strip()) - 1
            if 0 <= idx < len(files):
                preview_file(files[idx]["key"])
            else:
                console.print("[yellow]Invalid file number.[/yellow]")
        elif act == "p" and page > 1:
            page -= 1
        elif act.startswith("d"):
            arg = act[1:].strip()
            if arg in ("all", "*", ""):
                to_dl = [{"key": f["key"], "size": f["size"]} for f in files]
            else:
                idxs = parse_selection(arg, len(files))
                to_dl = [{"key": files[i - 1]["key"], "size": files[i - 1]["size"]}
                         for i in idxs]
            if to_dl:
                download_keys(to_dl, output_dir, workers, skip_confirm)
            break
        elif act in ("b", "back"):
            break


# ── Main loop ────────────────────────────────────────────────────────────────

def load_data():
    """Load all categories and file data from the API (single call)."""
    with console.status("[bold cyan]Loading data…"):
        data = api_get("/api/tui/files")
    categories = data["categories"]
    file_counts = data["file_counts"]
    total_datasets = data["total"]
    # Convert API dicts to (uploader, filename, key, size) tuples used internally
    all_files = {
        cat: [(f["uploader"], f["filename"], f["key"], f["size"]) for f in files]
        for cat, files in data["files"].items()
    }
    return categories, all_files, file_counts, total_datasets


def interactive(output_dir="downloads", workers=DEFAULT_WORKERS,
                skip_confirm=False):
    console.print(Panel.fit(
        "[bold white]datasite[/bold white]  Dataset Downloader",
        subtitle="browse · search · download · preview",
        border_style="cyan", padding=(1, 4),
    ))

    if not ensure_authenticated():
        console.print("[red]Authentication failed.[/red]")
        return
    if not acquire_session():
        return

    try:
        categories, all_files, file_counts, total_datasets = load_data()
    except APILimitError as e:
        console.print(f"[red]Access denied: {e}[/red]")
        return

    if not categories:
        console.print("[red]No categories found.[/red]")
        return

    while True:
        console.print()
        show_categories(categories, file_counts)
        console.print(
            f"\n[dim]{total_datasets} total datasets across "
            f"{len(categories)} categories[/dim]\n"
        )
        console.print("[bold]Commands:[/bold]")
        console.print("  [yellow]1-N[/yellow]     download category  "
                       "(multi: [bold]1,3,5[/bold] or [bold]2-4[/bold])")
        console.print("  [yellow]l N[/yellow]     list & browse files in category N")
        console.print("  [yellow]0[/yellow]       download ALL datasets")
        console.print("  [yellow]g[/yellow]       gradual download (streaming batches)")
        console.print("  [yellow]s[/yellow]       search datasets")
        console.print("  [yellow]h[/yellow]       download history")
        console.print("  [yellow]r[/yellow]       refresh data")
        console.print("  [yellow]q[/yellow]       quit\n")

        choice = Prompt.ask("[bold cyan]›").strip().lower()

        try:
            if choice == "q":
                console.print("[dim]Bye![/dim]")
                break

            elif choice == "r":
                categories, all_files, file_counts, total_datasets = load_data()
                console.print("[green]Refreshed![/green]")

            elif choice == "h":
                show_history()

            elif (choice.startswith("l") and len(choice) > 1
                  and choice[1:].strip().isdigit()):
                cat_num = int(choice[1:].strip())
                if 1 <= cat_num <= len(categories):
                    browse_category(categories[cat_num - 1], all_files,
                                    output_dir, workers, skip_confirm)
                else:
                    console.print("[yellow]Invalid category number.[/yellow]")

            elif choice == "s":
                query = Prompt.ask("[bold cyan]Search")
                if not query.strip():
                    continue
                with console.status("[bold cyan]Searching…"):
                    results = search(query, all_files)
                if not results:
                    console.print(f"[yellow]No results for '{query}'[/yellow]")
                    continue
                page = 1
                while True:
                    pages = show_search_results(results, page)
                    console.print(
                        "\n[bold]n[/bold]=next  [bold]p[/bold]=prev  "
                        "[bold]p N[/bold]=preview  "
                        "[bold]d 1,3,5[/bold]=download  "
                        "[bold]d all[/bold]=download all  [bold]b[/bold]=back"
                    )
                    act = Prompt.ask("[bold cyan]›").strip().lower()
                    if act in ("n", "next") and page < pages:
                        page += 1
                    elif (len(act) > 1 and act[0] == "p"
                          and act[1:].strip().isdigit()):
                        idx = int(act[1:].strip()) - 1
                        if 0 <= idx < len(results):
                            preview_file(results[idx]["key"])
                        else:
                            console.print("[yellow]Invalid number.[/yellow]")
                    elif act == "p" and page > 1:
                        page -= 1
                    elif act.startswith("d"):
                        arg = act[1:].strip()
                        if arg in ("all", "*", ""):
                            to_dl = [{"key": r["key"], "size": r["size"]}
                                     for r in results]
                        else:
                            idxs = parse_selection(arg, len(results))
                            to_dl = [{"key": results[i - 1]["key"],
                                      "size": results[i - 1]["size"]}
                                     for i in idxs]
                        if to_dl:
                            download_keys(to_dl, output_dir, workers, skip_confirm)
                        break
                    elif act in ("b", "back"):
                        break

            elif choice == "0":
                all_keys = [
                    {"key": f[2], "size": f[3] if len(f) > 3 else 0}
                    for cat_files in all_files.values()
                    for f in cat_files
                ]
                download_keys(all_keys, output_dir, workers, skip_confirm)

            elif choice == "g":
                # Gradual / streaming batch download
                bs = Prompt.ask("[bold cyan]Batch size (files per batch)",
                                default="10")
                try:
                    bs = max(1, int(bs))
                except ValueError:
                    bs = 10
                cat_choice = Prompt.ask(
                    "[bold cyan]Category number (or 'all')",
                    default="all"
                ).strip().lower()
                cat_filter = None
                if cat_choice not in ("all", "*", ""):
                    try:
                        cat_idx = int(cat_choice)
                        if 1 <= cat_idx <= len(categories):
                            cat_filter = categories[cat_idx - 1]
                    except ValueError:
                        cat_filter = cat_choice
                do_cleanup = Confirm.ask(
                    "[bold cyan]Delete files after each batch? (saves disk space)",
                    default=True
                )
                console.print("[yellow]Warning: callback commands are executed as shell commands.[/yellow]")
                cb = Prompt.ask(
                    "[bold cyan]Callback command per batch (blank for none)",
                    default=""
                ).strip() or None
                download_batch_streaming(
                    output_dir=output_dir, workers=workers,
                    batch_size=bs, category=cat_filter,
                    cleanup=do_cleanup, callback=cb,
                    skip_confirm=skip_confirm,
                )

            else:
                nums = parse_selection(choice, len(categories))
                if not nums:
                    console.print("[yellow]Invalid selection.[/yellow]")
                    continue
                all_keys = []
                for n in nums:
                    cat = categories[n - 1]
                    all_keys.extend(
                        {"key": f[2], "size": f[3] if len(f) > 3 else 0}
                        for f in all_files.get(cat, [])
                    )
                download_keys(all_keys, output_dir, workers, skip_confirm)

        except APILimitError as e:
            console.print(f"[red]API limit reached: {e}[/red]")
            break


def main():
    parser = argparse.ArgumentParser(
        prog="datasite-download",
        description="Download datasets from datasite.",
    )
    parser.add_argument("-o", "--output", default="downloads",
                        help="Output directory (default: downloads)")
    parser.add_argument("-c", "--category", default=None,
                        help="Download a specific category (name or substring)")
    parser.add_argument("-s", "--search", default=None, dest="query",
                        help="Search and download matching files")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip download confirmation prompts")
    parser.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel download workers (default: {DEFAULT_WORKERS})")
    parser.add_argument("--stream", action="store_true",
                        help="Enable streaming batch mode (download-use-delete cycle)")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="Files per batch in streaming mode (default: 10)")
    parser.add_argument("--cleanup", action="store_true", default=True,
                        help="Delete files after each batch in streaming mode (default)")
    parser.add_argument("--no-cleanup", action="store_false", dest="cleanup",
                        help="Keep files after each batch in streaming mode")
    parser.add_argument("--callback", default=None,
                        help="Shell command to run on each batch directory (WARNING: executed as-is in a shell)")
    parser.add_argument("--login", action="store_true",
                        help="Force re-authentication via GitHub")
    parser.add_argument("--logout", action="store_true",
                        help="Clear stored credentials and exit")
    args = parser.parse_args()

    check_github_version()

    if args.logout:
        clear_token()
        console.print("[green]Logged out.[/green]")
        return

    if args.login:
        clear_token()
        console.print("[dim]Cleared stored credentials.[/dim]")

    if not ensure_authenticated():
        console.print("[red]Authentication failed.[/red]")
        sys.exit(1)

    if not acquire_session():
        sys.exit(1)

    try:
        # Non-interactive: --stream (streaming batch mode)
        if args.stream:
            console.print("[bold cyan]Streaming batch mode[/bold cyan]")
            download_batch_streaming(
                output_dir=args.output,
                workers=args.workers,
                batch_size=args.batch_size,
                category=args.category,
                cleanup=args.cleanup,
                callback=args.callback,
                skip_confirm=args.yes,
            )
            return

        # Non-interactive: --category
        if args.category:
            console.print(f"[bold cyan]Category: {args.category}[/bold cyan]")
            with console.status("[bold cyan]Loading data…"):
                data = api_get("/api/tui/files", category=args.category)
            keys = [
                {"key": f["key"], "size": f["size"]}
                for cat_files in data.get("files", {}).values()
                for f in cat_files
            ]
            if not keys:
                console.print(f"[red]No files found for category '{args.category}'[/red]")
                return
            console.print(f"[green]Found {len(keys)} files[/green]")
            download_keys(keys, args.output, args.workers, args.yes)
            return

        # Non-interactive: --search
        if args.query:
            console.print(f"[bold cyan]Searching: {args.query}[/bold cyan]")
            with console.status("[bold cyan]Loading data…"):
                data = api_get("/api/tui/files")
            all_files = {
                cat: [(f["uploader"], f["filename"], f["key"], f["size"]) for f in files]
                for cat, files in data["files"].items()
            }
            results = search(args.query, all_files)
            if not results:
                console.print(f"[red]No results for '{args.query}'[/red]")
                return
            console.print(f"[green]Found {len(results)} files[/green]")
            to_dl = [{"key": r["key"], "size": r["size"]} for r in results]
            download_keys(to_dl, args.output, args.workers, args.yes)
            return

        # Interactive mode
        interactive(args.output, args.workers, args.yes)

    except APILimitError as e:
        console.print(f"[red]Access denied: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Partial downloads saved.[/yellow]")
        sys.exit(1)
