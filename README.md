# Neurvance Downloader

[![License: MIT + Commons Clause](https://img.shields.io/badge/license-MIT%20%2B%20Commons%20Clause-blue.svg)](LICENSE)

Download AI training data bundles and query CC0 public-source data — all from the terminal.

Two tools in this repo:

| File | What it does |
|---|---|
| `download.py` | Download bundles, query bundle knowledge base, search CC0 sources |
| `cc0_content.py` | Python client for the CC0 public-source search API |
| `rag.py` | Minimal example: load API key from `.env` and run a CC0 search |

API endpoint: `https://neurvancebackend-f7utq.ondigitalocean.app`

---

## Requirements

Python 3.10+. Install dependencies:

```bash
pip install requests tqdm httpx python-dotenv
```

`tqdm` is optional (nicer progress bars without it the downloader falls back to plain text).

---

## Bundle Downloader

```bash
# List available bundles
python download.py --list

# Filter the list
python download.py --list --search medical

# Download a specific bundle, skip prompts, auto-extract
python download.py --bundle my-bundle-slug --yes --extract

# Interactive flow (prompts for login + bundle selection)
python download.py
```

### Login

Sessions are cached for 8 hours in `~/.neurvance/session.json`.

```bash
python download.py --github    # GitHub OAuth via browser
python download.py --email     # Email / password
python download.py --relogin   # Force re-login
```

### How downloads work

1. `POST /api/tui/download-bundle/start` — server builds or returns a cached ZIP.
2. Poll `GET /api/tui/download-bundle/status?job_id=…` until the signed URL is ready.
3. `HEAD` checks if the server supports `Range` requests.
4. Downloads with parallel chunked workers (default 8) or serial stream fallback.
5. Resumes partial downloads when the server supports ranges.
6. SHA256-verifies the completed file.
7. `--extract` unpacks the ZIP to `<output-dir>/<slug>/`.

---

## Bundle RAG (`--rag`)

Search the knowledge index built from your accessible bundles. Requires login.

```bash
python download.py --rag "transformer fine-tuning datasets"
python download.py --rag "clinical trial outcomes" --rag-top-k 10
python download.py --reindex   # rebuild the index
```

---

## CC0 Public-Source Search (`--cc0`)

Search 25+ real-time public data sources filtered by CC0 license patterns.
No login — just an API key from your [dashboard](https://neurvance.com/dashboard).

```bash
python download.py --cc0 "impressionist paintings" --key sk-...

# Or set the key as an env var
export CC0_CONTENT_API_KEY=sk-...
python download.py --cc0 "deep-sea organisms"
```

---

## All flags

| Flag | Purpose |
|---|---|
| `--email` / `--github` | Choose login method |
| `--relogin` | Ignore cached session |
| `--list` | List bundles and exit |
| `--search TERM` | Filter bundle list |
| `--type {text,image}` | Show only text or image bundles |
| `--bundle SLUG` | Download a specific bundle directly |
| `--output-dir PATH` | Save ZIP here (default `.`) |
| `--yes` / `-y` | Skip confirmation prompts |
| `--extract` | Unzip after download |
| `--workers N` | Parallel download workers (default 8) |
| `--no-parallel` | Force single-threaded download |
| `--quiet` / `-q` | Suppress non-essential output |
| `--rag QUERY` | Query bundle knowledge index (login required) |
| `--rag-top-k N` | Number of RAG results (default 5) |
| `--reindex` | Trigger bundle knowledge reindex |
| `--cc0 QUERY` | Search CC0 public sources (API key required) |
| `--key APIKEY` | API key for `--cc0` |

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `NEURVANCE_URL` | `https://neurvancebackend-f7utq.ondigitalocean.app` | API base URL |
| `NEURVANCE_AUTH_URL` | `https://neurvance.com` | Browser OAuth start URL |
| `NEURVANCE_API_MIN_INTERVAL` | `5` | Min seconds between API calls |
| `CC0_CONTENT_API_KEY` | — | API key for `--cc0` and `cc0_content.py` |

---

## CC0 Content Client (`cc0_content.py`)

A standalone Python client for the CC0 search API.

### Quick example (`rag.py`)

```bash
# put CC0_CONTENT_API_KEY=your_key in a .env file
python rag.py
```

### Sync usage

```python
from cc0_content import CC0Client

with CC0Client(api_key="sk-...") as client:
    result = client.search("history of rome")
    for chunk in result["chunks"]:
        print(chunk["title"], chunk["source_url"])
        print(chunk["text"][:300])
```

### Async usage

```python
from cc0_content import AsyncCC0Client

async def run():
    async with AsyncCC0Client(api_key="sk-...") as client:
        result = await client.search("deep sea creatures")
        for chunk in result["chunks"]:
            print(chunk["title"])
```

### Methods

| Method | Description |
|---|---|
| `search(query)` | Search across public CC0 sources |
| `list_sources()` | List available sources and license basis |
| `health()` | Health check (no auth required) |

Each result chunk contains: `text`, `title`, `source_url`, `source_name`,
`license`, `license_url`, `content_type`, `relevance_score`, `metadata`,
`commercial_use_verified`, `jurisdiction_note`, `verification_confidence`.

---

## Dataset Catalog

See [DATASETS.md](DATASETS.md) for the full catalog organized by domain.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Rate limited; retrying in Ns…` | Increase `NEURVANCE_API_MIN_INTERVAL` |
| `SHA256 MISMATCH!` | Re-run; if it persists open an issue with the bundle slug |
| Browser login times out | Port 8765 needed for OAuth callback — free it or use `--email` |
| Resume re-downloads from scratch | Build URL expired or server has no range support — delete the partial `.zip` and retry |
| `--cc0 requires an API key` | Pass `--key APIKEY` or set `CC0_CONTENT_API_KEY` |
| CC0 search returns 401 | API key invalid or out of credits — check your dashboard |

---

## License

MIT + [Commons Clause](https://commonsclause.com/). Free to use and modify, including inside commercial products. You cannot sell the software itself as a standalone product or service.
