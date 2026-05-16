import argparse
import concurrent.futures
import getpass
import hashlib
import json
import os
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import requests
except ImportError:
    sys.exit("Error: 'requests' is not installed. Run: pip install requests")

try:
    from tqdm import tqdm as _tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("NEURVANCE_URL", "https://neurvance-bb82540cb249.herokuapp.com").rstrip("/")
API_MIN_INTERVAL = max(0.0, float(os.environ.get("NEURVANCE_API_MIN_INTERVAL", "5")))
DOWNLOAD_CHUNK = 1024 * 1024  # 1 MB
SESSION_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".neurvance", "session.json")
SESSION_MAX_AGE = 8 * 3600  # 8 hours
_last_api_request_at = 0.0

OAUTH_CALLBACK_PORT = 8765


# ── Args ──────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        prog="download_client.py",
        description="Neurvance Bundle Downloader — downloads AI training data bundles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python download_client.py --list\n"
            "  python download_client.py --list --search medical\n"
            "  python download_client.py --bundle my-bundle --yes --output-dir ./downloads\n"
            "  python download_client.py --email --type text --extract\n"
            "  python download_client.py --github --workers 16\n"
        ),
    )
    auth = p.add_mutually_exclusive_group()
    auth.add_argument("--email", action="store_true", help="Log in with email/password")
    auth.add_argument("--github", action="store_true", help="Log in via GitHub (browser)")
    p.add_argument("--relogin", action="store_true", help="Ignore cached session and re-authenticate")
    p.add_argument("--bundle", metavar="SLUG", help="Bundle slug to download directly (skip listing)")
    p.add_argument("--type", choices=["text", "image"], dest="bundle_type", help="Bundle type to show")
    p.add_argument("--list", action="store_true", dest="list_only", help="List available bundles and exit")
    p.add_argument("--search", metavar="TERM", help="Filter bundle list by name or slug")
    p.add_argument("--output-dir", metavar="PATH", default=".", help="Directory to save downloads (default: .)")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")
    p.add_argument("--extract", action="store_true", help="Auto-extract ZIP after download")
    p.add_argument("--workers", type=int, default=8, metavar="N",
                   help="Parallel download workers (default: 8; requires server Range support)")
    p.add_argument("--no-parallel", action="store_true", help="Force single-threaded serial download")
    p.add_argument("--quiet", "-q", action="store_true", help="Suppress non-essential output")
    return p.parse_args()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_banner(quiet=False):
    if quiet:
        return
    print()
    print("  ╔═══════════════════════════════════╗")
    print("  ║     Neurvance Bundle Downloader   ║")
    print("  ╚═══════════════════════════════════╝")
    print(f"  Server: {BASE_URL}")
    print()


def _log(msg, quiet=False, **kwargs):
    if not quiet:
        print(msg, **kwargs)


def _api(method, path, session_token=None, **kwargs):
    global _last_api_request_at
    url = f"{BASE_URL}{path}"
    headers = kwargs.pop("headers", {})
    if session_token:
        headers["X-Session-Token"] = session_token
    for attempt in range(4):
        if API_MIN_INTERVAL > 0:
            elapsed = time.time() - _last_api_request_at
            if elapsed < API_MIN_INTERVAL:
                time.sleep(API_MIN_INTERVAL - elapsed)
        _last_api_request_at = time.time()
        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code != 429 or attempt == 3:
            return resp
        retry_after = _retry_after_seconds(resp, API_MIN_INTERVAL)
        print(f"  Rate limited; retrying in {retry_after:.0f}s...")
        time.sleep(retry_after)
    return resp


def _retry_after_seconds(resp, default):
    raw = resp.headers.get("Retry-After", "")
    try:
        return max(float(raw), float(default))
    except (TypeError, ValueError):
        return float(default)


def _fmt_size(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_tokens(n):
    if n is None:
        return "?"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B tok"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M tok"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k tok"
    return f"{n} tok"


# ── Session cache ─────────────────────────────────────────────────────────────

def _load_cached_session():
    try:
        with open(SESSION_CACHE_PATH) as f:
            data = json.load(f)
        token = data.get("session_token", "")
        if token and (time.time() - data.get("saved_at", 0)) < SESSION_MAX_AGE:
            return token
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def _save_cached_session(session_token):
    try:
        os.makedirs(os.path.dirname(SESSION_CACHE_PATH), exist_ok=True)
        with open(SESSION_CACHE_PATH, "w") as f:
            json.dump({"session_token": session_token, "saved_at": time.time()}, f)
    except OSError:
        pass


def _validate_cached_session(session_token):
    try:
        resp = _api("GET", "/api/bundles", session_token=session_token)
        return resp.status_code == 200
    except Exception:
        return False


# ── Login: GitHub OAuth (browser) ─────────────────────────────────────────────

def _login_github():
    callback_url = f"http://localhost:{OAUTH_CALLBACK_PORT}/callback"
    login_url = f"{BASE_URL}/auth/github/login?tui_redirect={callback_url}"

    received = {}
    server_done = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                params = parse_qs(parsed.query)
                received["token"] = (params.get("token") or [""])[0]
                body = b"<html><body><h2>Login successful! You can close this tab.</h2></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()
            server_done.set()

        def log_message(self, *_):
            pass

    httpd = HTTPServer(("127.0.0.1", OAUTH_CALLBACK_PORT), _Handler)
    httpd.timeout = 1

    def _serve():
        deadline = time.time() + 120
        while not server_done.is_set() and time.time() < deadline:
            httpd.handle_request()
        httpd.server_close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    print(f"  Opening your browser for GitHub login…")
    print(f"  If it doesn't open, visit:\n  {login_url}")
    webbrowser.open(login_url)

    print("  Waiting for browser login (timeout 120 s)…", end=" ", flush=True)
    server_done.wait(timeout=120)

    bearer = received.get("token", "")
    if not bearer:
        print("TIMED OUT")
        sys.exit("  Browser login did not complete. Try again or use email login.")

    print("OK")
    print("  Creating session…", end=" ", flush=True)
    resp = _api("POST", "/api/tui/session", headers={"Authorization": f"Bearer {bearer}"})
    if resp.status_code != 200:
        print("FAILED")
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        sys.exit(f"  Session error: {detail}")

    data = resp.json()
    print("OK")
    return data["session_token"], data.get("calls_remaining", "?")


# ── Login: Email / Password ───────────────────────────────────────────────────

def _login_email():
    print("  Enter your Neurvance account credentials.")
    email = input("  Email: ").strip()
    if not email:
        sys.exit("No email entered. Exiting.")
    password = getpass.getpass("  Password: ").strip()
    if not password:
        sys.exit("No password entered. Exiting.")

    print("  Signing in…", end=" ", flush=True)
    resp = _api("POST", "/auth/email/login", json={"email": email, "password": password})
    if resp.status_code != 200:
        print("FAILED")
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        sys.exit(f"  Login error: {detail}")

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        print("FAILED")
        sys.exit("  Login error: no access token in response.")

    print("OK")
    print("  Creating session…", end=" ", flush=True)
    resp2 = _api("POST", "/api/tui/session", headers={"Authorization": f"Bearer {access_token}"})
    if resp2.status_code != 200:
        print("FAILED")
        try:
            detail = resp2.json().get("detail", resp2.text)
        except Exception:
            detail = resp2.text
        sys.exit(f"  Session error: {detail}")

    session_data = resp2.json()
    print("OK")
    return session_data["session_token"], session_data.get("calls_remaining", "?")


# ── Bundle listing ────────────────────────────────────────────────────────────

def _list_bundles(session_token):
    resp = _api("GET", "/api/bundles", session_token=session_token)
    if resp.status_code != 200:
        sys.exit(f"  Could not fetch bundles: {resp.text}")
    data = resp.json()
    return data.get("text_bundles", []), data.get("image_bundles", [])


def _filter_bundles(bundles, search):
    if not search:
        return bundles
    term = search.lower()
    return [b for b in bundles if
            term in (b.get("name") or "").lower() or
            term in (b.get("slug") or "").lower()]


def _print_bundles(bundles, quiet=False):
    if quiet:
        return
    print()
    print(f"  {'#':<4} {'Name':<40} {'Files':<7} {'Download':>10}  {'Tokens':>10}")
    print("  " + "─" * 74)
    for i, b in enumerate(bundles, 1):
        name = (b.get("name") or b.get("slug") or "")[:38]
        file_count = b.get("file_count") or 0
        downloadable = b.get("downloadable", True)
        if file_count > 0:
            files = str(file_count)
        elif downloadable:
            files = "✓"      # has files; count not exposed by server
        else:
            files = "none"
        tok = b.get("total_tokens_approx") or 0
        tokens = _fmt_tokens(tok) if tok > 0 else "pending"
        available = "" if downloadable else " (unavailable)"
        print(f"  {i:<4} {name:<40} {files:<7} {'Free':>10}  {tokens:>10}{available}")
    print()


# ── Download flow ─────────────────────────────────────────────────────────────

def _start_download(session_token, slug):
    req_id = str(uuid.uuid4())
    resp = _api(
        "POST", "/api/tui/download-bundle/start",
        session_token=session_token,
        json={"slug": slug, "request_id": req_id},
    )
    if resp.status_code == 200:
        data = resp.json()
        if data.get("url"):
            return data  # cache hit — already ready
    if resp.status_code == 202:
        return resp.json()
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    sys.exit(f"  Download start failed: {detail}")


def _poll_status(session_token, job_id, quiet=False):
    delay = 2.0  # adaptive: starts fast, backs off to 10 s
    while True:
        resp = _api(
            "GET", f"/api/tui/download-bundle/status?job_id={job_id}",
            session_token=session_token,
        )
        if resp.status_code not in (200, 202):
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            sys.exit(f"  Status check failed: {detail}")

        data = resp.json()
        status = data.get("status", "")

        if data.get("url"):
            if not quiet:
                print()
            return data

        if status == "failed":
            sys.exit(f"  Build failed: {data.get('error', 'unknown error')}")

        prog = data.get("progress", {})
        files_done = prog.get("files_done", 0)
        files_total = prog.get("files_total", "?")
        if not quiet:
            print(f"\r  Building… {files_done}/{files_total} files   ", end="", flush=True)

        time.sleep(delay)
        delay = min(delay + 2.0, 10.0)


# ── SHA256 helpers ────────────────────────────────────────────────────────────

def _sha256_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _sha256_partial(path, length):
    """Return a hashlib object seeded with the first `length` bytes of path."""
    sha = hashlib.sha256()
    remaining = length
    with open(path, "rb") as f:
        while remaining > 0:
            chunk = f.read(min(DOWNLOAD_CHUNK, remaining))
            if not chunk:
                break
            sha.update(chunk)
            remaining -= len(chunk)
    return sha


def _verify_and_log(out_path, received, expected_sha256, quiet, digest=None):
    if digest is None:
        digest = _sha256_file(out_path)
    _log(f"  Saved: {out_path}  ({_fmt_size(received)})", quiet)
    if expected_sha256:
        if digest.lower() == expected_sha256.lower():
            _log(f"  SHA256: {digest}  ✓", quiet)
        else:
            print(f"  SHA256 MISMATCH!")
            print(f"    expected: {expected_sha256}")
            print(f"    got:      {digest}")
    else:
        _log(f"  SHA256: {digest}", quiet)


# ── Download implementations ──────────────────────────────────────────────────

def _serial_stream(url, out_path, total, expected_sha256, quiet, start_offset=0):
    """Stream download (serial). Appends if start_offset > 0 (resume)."""
    headers = {}
    if start_offset:
        headers["Range"] = f"bytes={start_offset}-"

    resp = requests.get(url, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()

    sha = _sha256_partial(out_path, start_offset) if start_offset else hashlib.sha256()
    received = start_offset
    speed_bytes = 0
    speed_ts = time.time()

    bar = None
    if not quiet and total:
        if HAS_TQDM:
            bar = _tqdm(total=total, initial=start_offset, unit="B", unit_scale=True,
                        unit_divisor=1024, desc=f"  {os.path.basename(out_path)}", leave=True)

    mode = "ab" if start_offset else "wb"
    with open(out_path, mode) as f:
        for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK):
            if not chunk:
                continue
            f.write(chunk)
            sha.update(chunk)
            n = len(chunk)
            received += n
            if bar:
                bar.update(n)
            elif not quiet:
                speed_bytes += n
                now = time.time()
                dt = now - speed_ts
                if dt >= 1.0:
                    speed = speed_bytes / dt
                    speed_bytes = 0
                    speed_ts = now
                    if total:
                        pct = received * 100 // total
                        print(f"\r  {_fmt_size(received)} / {_fmt_size(total)}  ({pct}%)  {_fmt_size(speed)}/s",
                              end="", flush=True)
                    else:
                        print(f"\r  {_fmt_size(received)}  {_fmt_size(speed)}/s", end="", flush=True)

    if bar:
        bar.close()
    if not quiet:
        print()

    _verify_and_log(out_path, received, expected_sha256, quiet, digest=sha.hexdigest())


def _parallel_download(url, out_path, total, workers, expected_sha256, quiet):
    """Parallel chunked download using HTTP Range requests."""
    chunk_size = total // workers
    ranges = [
        (i * chunk_size, (i + 1) * chunk_size - 1 if i < workers - 1 else total - 1)
        for i in range(workers)
    ]

    # Pre-allocate file
    with open(out_path, "wb") as f:
        f.seek(total - 1)
        f.write(b"\0")

    lock = threading.Lock()
    received_total = [0]
    speed_bytes = [0]
    speed_ts = [time.time()]

    bar = None
    if not quiet and HAS_TQDM:
        bar = _tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024,
                    desc=f"  {os.path.basename(out_path)}", leave=True)

    def fetch_range(start, end):
        resp = requests.get(url, headers={"Range": f"bytes={start}-{end}"},
                            stream=True, timeout=300)
        resp.raise_for_status()
        data = b""
        for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK):
            if not chunk:
                continue
            data += chunk
            n = len(chunk)
            with lock:
                received_total[0] += n
                speed_bytes[0] += n
                if bar:
                    bar.update(n)
                elif not quiet:
                    now = time.time()
                    dt = now - speed_ts[0]
                    if dt >= 1.0:
                        speed = speed_bytes[0] / dt
                        speed_bytes[0] = 0
                        speed_ts[0] = now
                        pct = received_total[0] * 100 // total
                        print(f"\r  {_fmt_size(received_total[0])} / {_fmt_size(total)}"
                              f"  ({pct}%)  {_fmt_size(speed)}/s", end="", flush=True)
        return start, data

    errors = []
    with open(out_path, "r+b") as f:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {ex.submit(fetch_range, s, e): (s, e) for s, e in ranges}
            for future in concurrent.futures.as_completed(future_map):
                try:
                    start, data = future.result()
                    f.seek(start)
                    f.write(data)
                except Exception as exc:
                    errors.append(str(exc))

    if bar:
        bar.close()
    if not quiet:
        print()

    if errors:
        sys.exit(f"  Download failed: {errors[0]}")

    _verify_and_log(out_path, received_total[0], expected_sha256, quiet)


# ── Top-level download dispatcher ─────────────────────────────────────────────

def _download_zip(url, slug, expected_size=None, expected_sha256=None,
                  output_dir=".", workers=8, no_parallel=False, quiet=False, extract=False):
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{slug}.zip")

    # Check for an existing file
    existing = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if existing > 0 and expected_size and existing == expected_size:
        _log(f"\n  File already complete: {out_path}", quiet)
        digest = _sha256_file(out_path)
        if expected_sha256 and digest.lower() != expected_sha256.lower():
            _log("  SHA256 mismatch on existing file — re-downloading.", quiet)
            existing = 0
        else:
            _log(f"  SHA256: {digest}  ✓" if expected_sha256 else f"  SHA256: {digest}", quiet)
            if extract:
                _extract_zip(out_path, output_dir, slug, quiet)
            return out_path

    # HEAD to check Range support and confirm size
    accepts_ranges = False
    content_length = expected_size
    try:
        head = requests.head(url, timeout=15)
        accepts_ranges = head.headers.get("Accept-Ranges", "none").lower() != "none"
        cl = int(head.headers.get("Content-Length", 0))
        if cl:
            content_length = cl
    except Exception:
        pass

    _log(f"\n  Downloading to {out_path}…", quiet)

    # Resume partial download
    if existing > 0 and content_length and existing < content_length:
        if accepts_ranges:
            _log(f"  Resuming from {_fmt_size(existing)}…", quiet)
            _serial_stream(url, out_path, content_length, expected_sha256, quiet, start_offset=existing)
        else:
            _log("  Server doesn't support resume — re-downloading.", quiet)
            os.remove(out_path)
            existing = 0

    if existing == 0:
        use_parallel = accepts_ranges and not no_parallel and workers > 1 and content_length
        if use_parallel:
            _log(f"  Using {workers} parallel workers", quiet)
            _parallel_download(url, out_path, content_length, workers, expected_sha256, quiet)
        else:
            _serial_stream(url, out_path, content_length, expected_sha256, quiet)

    if extract:
        _extract_zip(out_path, output_dir, slug, quiet)
    return out_path


def _extract_zip(zip_path, output_dir, slug, quiet):
    extract_path = os.path.join(output_dir, slug)
    _log(f"  Extracting to {extract_path}…", quiet)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_path)
    _log(f"  Extracted: {extract_path}", quiet)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()
    quiet = args.quiet

    _print_banner(quiet)

    # ── Step 1: Login ──────────────────────────────────────────────────────────
    session_token = None

    if not args.relogin:
        session_token = _load_cached_session()
        if session_token:
            _log("  Using cached session…", quiet, end=" ", flush=True)
            if _validate_cached_session(session_token):
                _log("OK", quiet)
            else:
                _log("expired — re-authenticating.", quiet)
                session_token = None

    if session_token is None:
        if args.email:
            login_choice = "1"
        elif args.github:
            login_choice = "2"
        else:
            print("  How would you like to log in?")
            print("  [1] Email / Password")
            print("  [2] GitHub (browser)")
            print()
            login_choice = input("  Enter 1 or 2: ").strip()

        if login_choice == "1":
            session_token, _ = _login_email()
        elif login_choice == "2":
            session_token, _ = _login_github()
        else:
            sys.exit("  Invalid choice. Exiting.")

        _save_cached_session(session_token)
        _log("\n  Logged in.", quiet)

    # ── Step 2: Fetch bundles ──────────────────────────────────────────────────
    _log("  Fetching available bundles…", quiet)
    text_bundles, image_bundles = _list_bundles(session_token)

    if not text_bundles and not image_bundles:
        sys.exit("  No bundles available.")

    # ── --list mode ────────────────────────────────────────────────────────────
    if args.list_only:
        for label, pool in (("Text", text_bundles), ("Image", image_bundles)):
            displayed = _filter_bundles(pool, args.search)
            if args.search:
                print(f"  {label} bundles — {len(displayed)} match(es) for '{args.search}':")
            else:
                print(f"  {label} bundles ({len(pool)}):")
            _print_bundles(displayed)
        return

    # ── --bundle SLUG shortcut ─────────────────────────────────────────────────
    if args.bundle:
        all_bundles = text_bundles + image_bundles
        matches = [b for b in all_bundles if b.get("slug") == args.bundle]
        if not matches:
            sys.exit(f"  Bundle '{args.bundle}' not found.")
        selected = matches[0]
    else:
        # ── Step 3: Choose bundle type ─────────────────────────────────────────
        if args.bundle_type == "text":
            type_choice = "1"
        elif args.bundle_type == "image":
            type_choice = "2"
        else:
            print()
            print(f"  [1] Text bundles   ({len(text_bundles)} available)")
            print(f"  [2] Image bundles  ({len(image_bundles)} available)")
            print()
            type_choice = input("  Select bundle type (or 'q' to quit): ").strip().lower()
            if type_choice == "q":
                print("  Exiting.")
                return

        if type_choice == "1":
            pool = text_bundles
            kind = "text"
        elif type_choice == "2":
            pool = image_bundles
            kind = "image"
        else:
            sys.exit("  Invalid choice. Exiting.")

        if not pool:
            sys.exit(f"  No {kind} bundles available.")

        displayed = _filter_bundles(pool, args.search)
        if args.search:
            print(f"  Showing {len(displayed)} match(es) for '{args.search}'")
        if not displayed:
            sys.exit(f"  No bundles match '{args.search}'.")
        _print_bundles(displayed, quiet)

        # ── Step 4: Pick a bundle ──────────────────────────────────────────────
        while True:
            raw = input("  Enter bundle number to download (or 'q' to quit): ").strip()
            if raw.lower() == "q":
                print("  Exiting.")
                return
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(displayed):
                    selected = displayed[idx]
                    break
            print(f"  Please enter a number between 1 and {len(displayed)}.")

    name = selected.get("name") or selected.get("slug")
    slug = selected.get("slug")

    if not selected.get("downloadable"):
        print(f"\n  Warning: '{name}' has no files assigned yet. The download will be empty.")

    print()
    if not args.yes:
        confirm = input(f"  Download '{name}' for free? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return

    # ── Step 5: Start build ────────────────────────────────────────────────────
    _log("  Starting bundle build…", quiet)
    result = _start_download(session_token, slug)

    # ── Step 6: Poll if not immediately ready ──────────────────────────────────
    if not result.get("url"):
        job_id = result.get("job_id")
        if not job_id:
            sys.exit(f"  Unexpected response: {result}")
        result = _poll_status(session_token, job_id, quiet)

    # ── Step 7: Download ───────────────────────────────────────────────────────
    _download_zip(
        result["url"],
        slug,
        expected_size=result.get("size"),
        expected_sha256=result.get("sha256"),
        output_dir=args.output_dir,
        workers=args.workers,
        no_parallel=args.no_parallel,
        quiet=quiet,
        extract=args.extract,
    )

    _log("\n  Done!", quiet)


if __name__ == "__main__":
    main()
