import getpass
import hashlib
import json
import os
import sys
import threading
import time
import uuid
import webbrowser
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
POLL_INTERVAL = 3  # seconds between status polls
DOWNLOAD_CHUNK = 8192


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_banner():
    print()
    print("  ╔═══════════════════════════════════╗")
    print("  ║     Neurvance Bundle Downloader   ║")
    print("  ╚═══════════════════════════════════╝")
    print(f"  Server: {BASE_URL}")
    print()


def _api(method, path, session_token=None, **kwargs):
    url = f"{BASE_URL}{path}"
    headers = kwargs.pop("headers", {})
    if session_token:
        headers["X-Session-Token"] = session_token
    resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    return resp


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


OAUTH_CALLBACK_PORT = 8765


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


def _print_bundles(bundles):
    print()
    print(f"  {'#':<4} {'Name':<40} {'Files':<7} {'Price':>8}  {'Tokens':>10}")
    print("  " + "─" * 74)
    for i, b in enumerate(bundles, 1):
        name = (b.get("name") or b.get("slug") or "")[:38]
        files = b.get("file_count", 0)
        price = b.get("price_api_keys", "?")
        tokens = _fmt_tokens(b.get("total_tokens_approx"))
        available = "" if b.get("downloadable") else " (no files)"
        print(f"  {i:<4} {name:<40} {files:<7} {str(price):>7}c  {tokens:>10}{available}")
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
            return data  # already ready (cache hit)
    if resp.status_code == 202:
        return resp.json()
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    sys.exit(f"  Download start failed: {detail}")


def _poll_status(session_token, job_id):
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
            return data

        if status == "failed":
            sys.exit(f"  Build failed: {data.get('error', 'unknown error')}")

        prog = data.get("progress", {})
        files_done = prog.get("files_done", 0)
        files_total = prog.get("files_total", "?")
        print(f"\r  Building… {files_done}/{files_total} files   ", end="", flush=True)
        time.sleep(POLL_INTERVAL)


def _download_zip(url, slug, expected_size=None, expected_sha256=None):
    out_path = f"{slug}.zip"
    print(f"\n  Downloading to {out_path}…")

    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()

    total = expected_size or int(resp.headers.get("Content-Length", 0)) or None
    sha = hashlib.sha256()
    received = 0

    if HAS_TQDM and total:
        bar = _tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024,
                    desc="  " + out_path, leave=True)
    else:
        bar = None

    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK):
            if not chunk:
                continue
            f.write(chunk)
            sha.update(chunk)
            received += len(chunk)
            if bar:
                bar.update(len(chunk))
            elif total:
                pct = received * 100 // total
                print(f"\r  {_fmt_size(received)} / {_fmt_size(total)}  ({pct}%)", end="", flush=True)
            else:
                print(f"\r  {_fmt_size(received)}", end="", flush=True)

    if bar:
        bar.close()

    digest = sha.hexdigest()
    print(f"\n  Saved: {out_path}  ({_fmt_size(received)})")
    if expected_sha256:
        if digest.lower() == expected_sha256.lower():
            print(f"  SHA256: {digest}  ✓")
        else:
            print(f"  SHA256 MISMATCH!")
            print(f"    expected: {expected_sha256}")
            print(f"    got:      {digest}")
    else:
        print(f"  SHA256: {digest}")

    return out_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    _print_banner()

    # ── Step 1: Login ──────────────────────────────────────────────────────────
    print("  How would you like to log in?")
    print("  [1] Email / Password")
    print("  [2] GitHub (browser)")
    print()
    choice = input("  Enter 1 or 2: ").strip()
    if choice == "1":
        session_token, balance = _login_email()
    elif choice == "2":
        session_token, balance = _login_github()
    else:
        sys.exit("  Invalid choice. Exiting.")
    print(f"\n  Logged in.  Credit balance: {balance:,} credits" if isinstance(balance, int) else f"\n  Logged in.  Credit balance: {balance} credits")

    # ── Step 2: Fetch bundles ──────────────────────────────────────────────────
    print("  Fetching available bundles…")
    text_bundles, image_bundles = _list_bundles(session_token)

    if not text_bundles and not image_bundles:
        sys.exit("  No bundles available.")

    # ── Step 3: Choose bundle type ─────────────────────────────────────────────
    print()
    print(f"  [1] Text bundles   ({len(text_bundles)} available)")
    print(f"  [2] Image bundles  ({len(image_bundles)} available)")
    print()
    type_choice = input("  Select bundle type (or 'q' to quit): ").strip().lower()
    if type_choice == "q":
        print("  Exiting.")
        return
    if type_choice == "1":
        bundles = text_bundles
        kind = "text"
    elif type_choice == "2":
        bundles = image_bundles
        kind = "image"
    else:
        sys.exit("  Invalid choice. Exiting.")

    if not bundles:
        sys.exit(f"  No {kind} bundles available.")

    _print_bundles(bundles)

    # ── Step 4: Pick a bundle ──────────────────────────────────────────────────
    while True:
        raw = input("  Enter bundle number to download (or 'q' to quit): ").strip()
        if raw.lower() == "q":
            print("  Exiting.")
            return
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(bundles):
                selected = bundles[idx]
                break
        print(f"  Please enter a number between 1 and {len(bundles)}.")

    name = selected.get("name") or selected.get("slug")
    price = selected.get("price_api_keys", "?")
    slug = selected.get("slug")

    if not selected.get("downloadable"):
        print(f"\n  Warning: '{name}' has no files assigned yet. The download will be empty.")

    print()
    confirm = input(f"  Download '{name}' for {price} credits? [y/N]: ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    # ── Step 5: Start build ────────────────────────────────────────────────────
    print("  Starting bundle build…")
    result = _start_download(session_token, slug)

    # ── Step 6: Poll if not immediately ready ──────────────────────────────────
    if not result.get("url"):
        job_id = result.get("job_id")
        if not job_id:
            sys.exit(f"  Unexpected response: {result}")
        result = _poll_status(session_token, job_id)

    # ── Step 7: Download ───────────────────────────────────────────────────────
    download_url = result.get("url")
    size = result.get("size")
    sha256 = result.get("sha256")

    _download_zip(download_url, slug, expected_size=size, expected_sha256=sha256)

    # ── Step 8: Show credit summary ────────────────────────────────────────────
    charged = result.get("credits_charged")
    remaining = result.get("calls_remaining")
    if charged is not None or remaining is not None:
        parts = []
        if charged is not None:
            parts.append(f"Credits used: {charged}")
        if remaining is not None:
            parts.append(f"Balance remaining: {remaining:,}" if isinstance(remaining, int) else f"Balance remaining: {remaining}")
        print(f"  {'.  '.join(parts)}")

    print()
    print("  Done!")


if __name__ == "__main__":
    main()
