# Neurvance Downloader

[![License: MIT + Commons Clause](https://img.shields.io/badge/license-MIT%20%2B%20Commons%20Clause-blue.svg)](LICENSE)
[![CC0](https://img.shields.io/badge/data-CC0%20licensed-orange)](https://creativecommons.org/publicdomain/zero/1.0/)

## Curated training data, bundle downloads, and CC0 search

Neurvance provides curated, pre-cleaned datasets through a terminal downloader and a small Python client for CC0 content search. The current repository includes:

- `download.py` - an interactive terminal downloader for text and image bundles.
- `cc0_content.py` - sync and async Python clients for the CC0 Content API.
- `rag.py` - a minimal example that loads an API key from `.env` and runs a search.

Browse bundles and subscribe at [neurvance.com](https://neurvance.com).

---

## Requirements

- Python 3.10 or newer
- A Neurvance account for bundle downloads
- A `CC0_CONTENT_API_KEY` for the CC0/RAG search client

Install the dependencies used by the current scripts:

```bash
pip install requests tqdm httpx python-dotenv
```

`tqdm` is optional for `download.py`; without it, the downloader falls back to a simple text progress display.

---

## Bundle Downloader

Run the interactive downloader:

```bash
python download.py
```

The downloader currently works as an interactive terminal flow:

1. Choose email/password login or GitHub browser login.
2. The client creates a short-lived TUI session with the Neurvance API.
3. Choose text bundles or image bundles.
4. Pick a bundle by number and confirm the credit cost.
5. The server builds or reuses a zip bundle.
6. The client downloads `<bundle-slug>.zip` into the current directory and prints the SHA256 hash.

The downloader uses this production API by default:

```text
https://neurvance-bb82540cb249.herokuapp.com
```

Override it for development or staging with:

```bash
export NEURVANCE_URL="https://your-api.example.com"
python download.py
```

### Current Downloader Notes

- There is no non-interactive `--bundle` or `--output-dir` mode in the current `download.py`.
- Downloads are saved as `<slug>.zip` in the directory where you run the script.
- GitHub login opens a local callback server on `http://localhost:8765/callback`.
- Email login posts to `/auth/email/login`.
- Bundle downloads use `/api/tui/download-bundle/start` and poll `/api/tui/download-bundle/status`.

---

## CC0 / RAG Search

The current RAG example uses `CC0Client.search()` from `cc0_content.py`. It requires an API key.

Create a `.env` file:

```bash
CC0_CONTENT_API_KEY=your_api_key_here
```

Run the included example:

```bash
python rag.py
```

By default, `rag.py` searches for:

```text
history of rome
```

Edit the query in `rag.py` to try a different search.

### Use The Client Directly

```python
from cc0_content import CC0Client

client = CC0Client(api_key="your_api_key_here")
results = client.search("medical imaging classification")
print(results["chunks"])
client.close()
```

With environment variables:

```python
from cc0_content import CC0Client

with CC0Client() as client:
    results = client.search("history of rome")
    print(results["chunks"])
```

Available client methods:

- `search(query)` - search CC0 content chunks.
- `list_sources()` - list available CC0 content sources.
- `health()` - check API health.

Optional environment variables:

```bash
export CC0_CONTENT_API_KEY="your_api_key_here"
export CC0_CONTENT_BASE_URL="https://your-api.example.com"
```

An async client is also available as `AsyncCC0Client`.

---

## Dataset Catalog

See [DATASETS.md](DATASETS.md) for the dataset catalog organized by domain.

---

## License

MIT + Commons Clause. You may use the software and data for personal and commercial AI training. You may not sell, sublicense, or distribute the software itself for a fee.
