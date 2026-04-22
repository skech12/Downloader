# Neurvance Downloader

[![License: MIT + Commons Clause](https://img.shields.io/badge/license-MIT%20%2B%20Commons%20Clause-blue.svg)](LICENSE)
[![CC0](https://img.shields.io/badge/data-CC0%20licensed-orange)](https://creativecommons.org/publicdomain/zero/1.0/)

## Curated training data and RAG infrastructure for production ML

Neurvance delivers curated, pre-cleaned datasets through a terminal client — no scraping, no formatting, no data prep. You browse bundles, pick what you need, and download a ready-to-train zip.

→ **[Browse bundles and subscribe at neurvance.com](https://neurvance.com)**

---

## How It Works

1. **Install dependencies**

   ```bash
   pip install requests rich python-dotenv
   ```

2. **Run the downloader**

   ```bash
   python download.py
   ```

   A browser window opens for GitHub login. Once authenticated, you'll see the full bundle catalog in your terminal.

3. **Browse and download**

   Pick a bundle by number, preview its files and size, then confirm to download. The server scrubs PII, toxicity, and bias filters in transit — the zip arrives clean.

---

## Non-interactive download

```bash
python download.py --bundle <slug> --output-dir ./data
```

---

## RAG Search

Query the catalog with natural language using `rag.py`:

```bash
python rag.py -q "medical imaging classification"
```

---

## CC0 Content API

`cc0_content.py` is a single-file Python client for the CC0 Content API:

```python
from cc0_content import CC0Client

client = CC0Client()  # opens browser for login on first run
results = client.search("history of rome")
print(results["chunks"])
```

---

## Pricing

**Starter — €49/month**, billed monthly. Cancel anytime.

Includes access to the full bundle catalog via the Downloader client and RAG queries.

Get your API key at **[neurvance.com](https://neurvance.com)**

---

## Dataset Catalog

See [DATASETS.md](DATASETS.md) for the full searchable list organized by domain.

---

## License

MIT + Commons Clause. Free for personal and commercial AI training use. You cannot sell the software itself.
