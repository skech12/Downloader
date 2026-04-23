"""Neurvance Datasite TUI — bundle browser + downloader.

The catalog source-of-truth is Supabase: each bundle is a curated set of
``files`` rows whose ``download_link`` points at an upstream host (HuggingFace,
Zenodo, GitHub release, etc.). The backend streams the bundle as a zip,
scrubbing PII/GDPR text in flight; the TUI just receives bytes and writes
them to disk. Each download costs 1 API call — repeats are not free.

Nothing about the catalog is cached client-side: every browse hits the API.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import threading
import warnings
import webbrowser
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote as _url_quote

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* doesn't match a supported version!",
    category=Warning,
    module=r"requests(\..*)?$",
)

import requests
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

console = Console()

API_BASE_URL = os.getenv("DATASITE_API_URL", "https://neurvance-bb82540cb249.herokuapp.com")
APP_VERSION = "2.1.0"
DEFAULT_OUTPUT_DIR = Path.cwd() / "neurvance_downloads"
TOKEN_FILE = Path.home() / ".datasite" / "config.json"

_bearer_token = ""
_session_token = ""


class APIError(Exception):
    pass


# ── Token storage ────────────────────────────────────────────────────────────

def load_token() -> str:
    try:
        if TOKEN_FILE.exists():
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("token", "")
    except (json.JSONDecodeError, OSError):
        pass
    return ""


def save_token(token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"token": token}), encoding="utf-8")


def clear_token() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


# ── Auth (GitHub OAuth via local callback) ───────────────────────────────────

def github_login_flow() -> str:
    result = {"token": ""}
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import parse_qs, urlparse

            params = parse_qs(urlparse(self.path).query)
            tok = params.get("token", [""])[0]
            if tok:
                result["token"] = tok
                body = (
                    b"<html><body style='font-family:sans-serif;margin:40px'>"
                    b"<h2>Login successful!</h2><p>You can close this tab.</p></body></html>"
                )
            else:
                import html as _html

                err = _html.escape(params.get("error", ["Unknown error"])[0])
                body = (
                    f"<html><body style='font-family:sans-serif;margin:40px'>"
                    f"<h2>Login failed</h2><p>{err}</p></body></html>"
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *_args):
            return

    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
        port = srv.server_address[1]
        callback = f"http://127.0.0.1:{port}/callback"
        login_url = f"{API_BASE_URL}/auth/github/login?tui_redirect={_url_quote(callback)}"
        threading.Thread(target=srv.handle_request, daemon=True).start()
        console.print("\n[bold cyan]Opening browser for GitHub login...[/bold cyan]")
        console.print(f"[dim]If your browser doesn't open, visit:[/dim] {login_url}\n")
        webbrowser.open(login_url)
        if not done.wait(timeout=300):
            console.print("[red]Login timed out (5 min). Run the tool again to retry.[/red]")
            return ""
    return result["token"]


def ensure_authenticated() -> bool:
    global _bearer_token
    _bearer_token = load_token()
    if _bearer_token:
        return True
    console.print("[yellow]No stored credentials. Starting GitHub login...[/yellow]")
    tok = github_login_flow()
    if not tok:
        return False
    save_token(tok)
    _bearer_token = tok
    console.print("[green]Login successful![/green]")
    return True


def acquire_session() -> bool:
    """POST /api/tui/session — charges 1 API call, returns a session token."""
    global _session_token
    if _session_token:
        return True
    if not _bearer_token:
        console.print("[red]Not authenticated. Re-run with --login.[/red]")
        return False
    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/tui/session",
            headers={"Authorization": f"Bearer {_bearer_token}"},
            timeout=(5, 30),
        )
    except requests.exceptions.ConnectionError:
        console.print(f"[red]Cannot connect to server at {API_BASE_URL}[/red]")
        return False
    if resp.status_code == 401:
        console.print("[yellow]Token expired. Re-authenticating...[/yellow]")
        clear_token()
        return ensure_authenticated() and acquire_session()
    if resp.status_code == 403:
        console.print(f"[red]{resp.json().get('detail', 'No API calls remaining.')}[/red]")
        return False
    resp.raise_for_status()
    data = resp.json()
    _session_token = data["session_token"]
    console.print(
        f"[green]Session started — 1 API call charged, "
        f"{data.get('calls_remaining', '?')} remaining[/green]"
    )
    return True


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _auth_headers() -> dict:
    if _session_token:
        return {"X-Session-Token": _session_token}
    if _bearer_token:
        return {"Authorization": f"Bearer {_bearer_token}"}
    return {}


def api_get(path: str, **params) -> dict:
    try:
        resp = requests.get(
            f"{API_BASE_URL}{path}",
            params={k: v for k, v in params.items() if v is not None},
            headers=_auth_headers(),
            timeout=(5, 60),
        )
    except requests.exceptions.ConnectionError:
        raise APIError(f"Cannot connect to {API_BASE_URL}")
    if resp.status_code in (401, 403):
        raise APIError(resp.json().get("detail", f"HTTP {resp.status_code}"))
    resp.raise_for_status()
    return resp.json()


# ── Formatting ───────────────────────────────────────────────────────────────

def fmt_size(n: int | float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_tokens(n: int | float) -> str:
    n = float(n or 0)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n)) if n else "-"


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ── Bundle browsing ──────────────────────────────────────────────────────────

def load_bundles(search: str | None = None) -> list[dict]:
    """Always fetches fresh — no client-side cache."""
    data = api_get("/api/bundles")
    bundles = data.get("bundles", [])
    if not search:
        return bundles
    q = search.lower().strip()
    out = []
    for b in bundles:
        hay = " ".join(
            [
                str(b.get("name") or ""),
                str(b.get("slug") or ""),
                str(b.get("description") or ""),
                " ".join(b.get("keywords") or []),
            ]
        ).lower()
        if q in hay:
            out.append(b)
    return out


def exclude_bundles(bundles: list[dict], terms: list[str]) -> list[dict]:
    """Remove bundles whose metadata contains any of the given terms."""
    if not terms:
        return bundles
    lower_terms = [t.lower().strip() for t in terms if t.strip()]
    out = []
    for b in bundles:
        hay = " ".join(
            [
                str(b.get("name") or ""),
                str(b.get("slug") or ""),
                str(b.get("description") or ""),
                " ".join(b.get("keywords") or []),
            ]
        ).lower()
        if not any(t in hay for t in lower_terms):
            out.append(b)
    return out


def render_bundle_table(bundles: list[dict]) -> None:
    table = Table(box=box.SIMPLE_HEAVY, show_lines=False, expand=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Name", style="bold")
    table.add_column("Files", justify="right", width=6)
    table.add_column("Size", justify="right", width=10)
    table.add_column("~Tokens", justify="right", width=9)
    table.add_column("Price", justify="right", width=8)
    table.add_column("Bio", overflow="fold")
    for idx, b in enumerate(bundles, start=1):
        size = b.get("total_size_approx") or 0
        tokens = b.get("total_tokens_approx") or 0
        table.add_row(
            str(idx),
            _truncate(b.get("name") or b.get("slug") or "(unnamed)", 40),
            str(b.get("file_count") or 0),
            fmt_size(size) if size else "-",
            fmt_tokens(tokens),
            f"${b.get('price_api_keys') or 0}",
            _truncate(b.get("description") or "", 70),
        )
    console.print(table)


def render_bundle_detail(slug: str) -> dict | None:
    bundle = api_get(f"/api/bundles/{slug}")
    summary = api_get(f"/api/bundles/{slug}/datasets")
    files_resp = api_get(f"/api/bundles/{slug}/files", offset=0, limit=200)

    header = Panel(
        f"[bold cyan]{bundle.get('name') or slug}[/bold cyan]\n\n"
        f"[white]{bundle.get('description') or '(no description)'}[/white]\n\n"
        f"[dim]use case:[/dim] {bundle.get('use_case') or '-'}\n"
        f"[dim]models:[/dim] {', '.join(bundle.get('recommended_models') or []) or '-'}\n"
        f"[dim]keywords:[/dim] {', '.join(bundle.get('keywords') or []) or '-'}\n\n"
        f"[bold]{summary.get('dataset_count') or 0}[/bold] files · "
        f"[bold]{fmt_size(summary.get('total_size_bytes') or 0)}[/bold] · "
        f"≈[bold]{summary.get('total_tokens_approx') or 0:,}[/bold] tokens",
        title=f"Bundle {slug}",
        border_style="cyan",
    )
    console.print(header)

    files = files_resp.get("files") or []
    table = Table(box=box.SIMPLE, show_lines=False, expand=True)
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("File", style="bold")
    table.add_column("Size", justify="right", width=10)
    table.add_column("License", width=14)
    table.add_column("Bio", overflow="fold")
    for idx, f in enumerate(files, start=1):
        table.add_row(
            str(idx),
            _truncate(f.get("name") or "(unnamed)", 50),
            fmt_size(f.get("size") or 0),
            _truncate(f.get("license") or "-", 14),
            _truncate(f.get("bio") or "", 50),
        )
    console.print(table)
    if files_resp.get("has_more"):
        console.print(f"[dim]…{files_resp.get('total') or 0} files total — showing first 200[/dim]")

    # File-type summary
    ext_counts: Counter = Counter()
    for f in files:
        name = f.get("name") or ""
        ext = Path(name).suffix.lower() or "(no ext)"
        ext_counts[ext] += 1
    if ext_counts:
        parts = ", ".join(f"[bold]{cnt}[/bold] × {ext}" for ext, cnt in ext_counts.most_common())
        console.print(f"[dim]Formats: {parts}[/dim]\n")

    return bundle


# ── Download ─────────────────────────────────────────────────────────────────


def _show_quality_report(zip_path: Path) -> None:
    """Extract and display QUALITY_REPORT.txt from the downloaded zip."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if "QUALITY_REPORT.txt" not in zf.namelist():
                return
            report = zf.read("QUALITY_REPORT.txt").decode("utf-8", errors="replace")
            console.print(
                Panel(
                    report,
                    title="[bold green]Data Quality Report[/bold green]",
                    border_style="green",
                    padding=(1, 2),
                )
            )
    except Exception:
        pass  # silently skip if zip is partial or report is missing


def download_bundle(slug: str, output_dir: Path, max_bytes: int | None = None) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{slug}.zip"
    partial = output_dir / f"{slug}.zip.partial"

    # Resume support: check for an existing partial download
    resume_from = 0
    if partial.exists():
        resume_from = partial.stat().st_size
        if resume_from > 0:
            if Confirm.ask(
                f"[yellow]Partial download found ({fmt_size(resume_from)}). Resume?[/yellow]",
                default=True,
            ):
                console.print(f"[dim]Resuming from byte {resume_from:,}[/dim]")
            else:
                resume_from = 0
                partial.unlink()
    elif target.exists():
        if not Confirm.ask(f"[yellow]{target.name} already exists. Overwrite?[/yellow]", default=False):
            return False

    console.print(
        f"\n[bold]Charging 1 API credit.[/bold] "
        f"Each download is charged separately, even repeats."
    )
    console.print(
        "[dim]Data is scrubbed in transit — PII, toxicity, language, quality, and bias "
        "filters all run server-side before the zip reaches you.[/dim]"
    )
    console.print("[dim]Download may be slower than raw network speed during cleaning.[/dim]\n")

    req_headers = {**_auth_headers()}
    if resume_from:
        req_headers["Range"] = f"bytes={resume_from}-"

    try:
        resp = requests.get(
            f"{API_BASE_URL}/api/tui/download-bundle",
            params={"slug": slug},
            headers=req_headers,
            stream=True,
            timeout=(10, None),
        )
    except requests.exceptions.ConnectionError:
        console.print(f"[red]Cannot connect to {API_BASE_URL}[/red]")
        return False

    if resp.status_code == 403:
        try:
            detail = resp.json().get("detail", "Forbidden.")
        except ValueError:
            detail = resp.text
        console.print(f"[red]{detail}[/red]")
        if "remaining" in str(detail).lower():
            console.print("[dim]Top up at https://neurvance.com[/dim]")
        return False
    if resp.status_code == 404:
        console.print(f"[red]Bundle not found: {slug}[/red]")
        return False
    if resp.status_code not in (200, 206):
        console.print(f"[red]HTTP {resp.status_code}: {resp.text[:200]}[/red]")
        return False

    remaining = resp.headers.get("X-Calls-Remaining")
    content_length = int(resp.headers.get("Content-Length") or 0)
    # For a resumed download, total is bytes already downloaded + remaining content
    total = resume_from + content_length if content_length else 0

    # GB cap check — compare against full bundle size
    if max_bytes and total and total > max_bytes:
        console.print(
            f"[yellow]Bundle is {fmt_size(total)} — exceeds your {fmt_size(max_bytes)} limit.[/yellow]"
        )
        if not Confirm.ask("Download anyway?", default=False):
            resp.close()
            return False

    progress = Progress(
        TextColumn("[bold blue]{task.fields[name]}", justify="right"),
        BarColumn(bar_width=None),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    bytes_written = resume_from
    file_mode = "ab" if resume_from else "wb"
    write_path = partial  # always write to .partial, rename on success
    try:
        with progress:
            task = progress.add_task(
                "download",
                name=f"{slug}.zip",
                total=total or None,
                completed=resume_from,
            )
            with write_path.open(file_mode) as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    bytes_written += len(chunk)
                    progress.update(task, advance=len(chunk))
    except KeyboardInterrupt:
        console.print(
            f"\n[yellow]Download interrupted — partial file kept at {partial}[/yellow]\n"
            "[dim]Re-run the same command to resume.[/dim]"
        )
        return False
    except Exception as exc:
        console.print(f"[red]Stream failed: {exc}[/red]")
        return False
    finally:
        try:
            resp.close()
        except Exception:
            pass

    # Rename .partial → .zip on successful completion
    if target.exists():
        target.unlink()
    partial.rename(target)

    console.print(
        f"[green]Saved {fmt_size(bytes_written)} → {target}[/green]"
        + (f"  [dim]({remaining} credits left)[/dim]" if remaining is not None else "")
    )
    _show_quality_report(target)
    return True


# ── Main loop ────────────────────────────────────────────────────────────────

def _print_header() -> None:
    console.print(
        Panel(
            "[bold cyan]Neurvance Datasite[/bold cyan] — bundle browser\n"
            f"[dim]API:[/dim] {API_BASE_URL}   [dim]v{APP_VERSION}[/dim]",
            border_style="cyan",
        )
    )


def interactive_loop(
    output_dir: Path,
    max_bytes: int | None = None,
    exclude_terms: list[str] | None = None,
    manifest_only: bool = False,
) -> None:
    _print_header()
    if not ensure_authenticated():
        return
    if not acquire_session():
        return

    active_exclusions: list[str] = list(exclude_terms or [])

    while True:
        console.print()
        try:
            bundles = load_bundles()
        except APIError as exc:
            console.print(f"[red]{exc}[/red]")
            return

        if active_exclusions:
            bundles = exclude_bundles(bundles, active_exclusions)
            console.print(f"[dim]Excluding keywords: {', '.join(active_exclusions)}[/dim]")

        if not bundles:
            console.print("[yellow]No bundles available.[/yellow]")
            return

        render_bundle_table(bundles)

        hint_parts = [
            "Pick a bundle by number",
            "type [bold]search <term>[/bold] to filter",
            "type [bold]exclude <term>[/bold] to hide bundles",
            "[bold]q[/bold] to quit",
        ]
        console.print("\n[dim]" + ", ".join(hint_parts) + ".[/dim]")
        choice = Prompt.ask("›", default="").strip()

        if not choice or choice.lower() in ("q", "quit", "exit"):
            return

        if choice.lower().startswith("exclude"):
            term = choice[7:].strip() or Prompt.ask("Exclude term").strip()
            if term:
                active_exclusions.append(term)
                console.print(f"[dim]Now excluding: {', '.join(active_exclusions)}[/dim]")
            continue

        if choice.lower().startswith("search"):
            term = choice[6:].strip() or Prompt.ask("Search term").strip()
            try:
                results = load_bundles(search=term)
            except APIError as exc:
                console.print(f"[red]{exc}[/red]")
                continue
            results = exclude_bundles(results, active_exclusions)
            if not results:
                console.print(f"[yellow]No bundles match '{term}'.[/yellow]")
                continue
            render_bundle_table(results)
            sub = Prompt.ask("Pick #", default="").strip()
            if not sub.isdigit():
                continue
            idx = int(sub)
            if idx < 1 or idx > len(results):
                continue
            slug = results[idx - 1].get("slug") or ""
        elif choice.isdigit():
            idx = int(choice)
            if idx < 1 or idx > len(bundles):
                continue
            slug = bundles[idx - 1].get("slug") or ""
        else:
            continue

        if not slug:
            continue

        try:
            render_bundle_detail(slug)
        except APIError as exc:
            console.print(f"[red]{exc}[/red]")
            continue

        if manifest_only:
            continue

        if Confirm.ask("Download this bundle?", default=True):
            try:
                download_bundle(slug, output_dir, max_bytes=max_bytes)
            except APIError as exc:
                console.print(f"[red]{exc}[/red]")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Neurvance Datasite TUI — bundle browser & downloader.")
    parser.add_argument("--login", action="store_true", help="Force GitHub re-login then exit.")
    parser.add_argument("--logout", action="store_true", help="Clear stored token then exit.")
    parser.add_argument("--bundle", metavar="SLUG", help="Download one bundle non-interactively.")
    parser.add_argument(
        "--bundles",
        metavar="SLUG1,SLUG2,...",
        help="Download multiple bundles non-interactively (comma-separated slugs).",
    )
    parser.add_argument(
        "--max-gb",
        type=float,
        default=None,
        metavar="N",
        help="Warn (and confirm) before downloading a bundle larger than N GB.",
    )
    parser.add_argument(
        "--exclude-keywords",
        default=None,
        metavar="KW1,KW2,...",
        help="Hide bundles whose metadata contains any of these comma-separated keywords.",
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Show bundle file list and quality report without downloading.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Where to save zips (default: {DEFAULT_OUTPUT_DIR}).",
    )
    args = parser.parse_args()

    if args.logout:
        clear_token()
        console.print("[green]Stored credentials cleared.[/green]")
        return 0

    if args.login:
        clear_token()
        if ensure_authenticated():
            console.print("[green]Logged in.[/green]")
            return 0
        return 1

    out_dir = Path(args.output_dir).expanduser()
    max_bytes = int(args.max_gb * 1024 ** 3) if args.max_gb else None
    exclude_terms = [t.strip() for t in args.exclude_keywords.split(",") if t.strip()] if args.exclude_keywords else []

    # Non-interactive single bundle
    if args.bundle:
        if not ensure_authenticated() or not acquire_session():
            return 1
        if args.manifest_only:
            try:
                render_bundle_detail(args.bundle)
                return 0
            except APIError as exc:
                console.print(f"[red]{exc}[/red]")
                return 1
        try:
            return 0 if download_bundle(args.bundle, out_dir, max_bytes=max_bytes) else 1
        except APIError as exc:
            console.print(f"[red]{exc}[/red]")
            return 1

    # Non-interactive multi-bundle batch
    if args.bundles:
        if not ensure_authenticated() or not acquire_session():
            return 1
        slugs = [s.strip() for s in args.bundles.split(",") if s.strip()]
        cumulative_bytes = 0
        ok = True
        for slug in slugs:
            if max_bytes and cumulative_bytes >= max_bytes:
                console.print(
                    f"[yellow]Reached {fmt_size(max_bytes)} limit — skipping remaining bundles.[/yellow]"
                )
                break
            remaining_cap = (max_bytes - cumulative_bytes) if max_bytes else None
            try:
                if args.manifest_only:
                    render_bundle_detail(slug)
                else:
                    success = download_bundle(slug, out_dir, max_bytes=remaining_cap)
                    if success:
                        zip_path = out_dir / f"{slug}.zip"
                        if zip_path.exists():
                            cumulative_bytes += zip_path.stat().st_size
                    else:
                        ok = False
            except APIError as exc:
                console.print(f"[red]{exc}[/red]")
                ok = False
        return 0 if ok else 1

    try:
        interactive_loop(
            out_dir,
            max_bytes=max_bytes,
            exclude_terms=exclude_terms,
            manifest_only=args.manifest_only,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
