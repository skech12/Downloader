# Neurvance Downloader

[![License: MIT + Commons Clause](https://img.shields.io/badge/license-MIT%20%2B%20Commons%20Clause-blue.svg)](LICENSE)
[![Datasets](https://img.shields.io/badge/datasets-12%2C500%2B-brightgreen)](https://neurvance.com/datasets)
[![Version](https://img.shields.io/pypi/v/neurvance-downloader?label=version)](https://pypi.org/project/neurvance-downloader)
[![CC0](https://img.shields.io/badge/data-CC0%20licensed-orange)](https://creativecommons.org/publicdomain/zero/1.0/)

## 12,500+ CC0 datasets for AI fine-tuning — download free or use the API

You write the training code. We handle the data pipeline.

Free to download manually. API key unlocks bulk and incremental access — so you never have to scrape, clean, or format training data again.

→ **[Browse the full dataset catalog on neurvance.com](https://neurvance.com/datasets)**

---

## Quick Start

```bash
pip install neurvance-downloader
```

```python
from neurvance import Neurvance

nv = Neurvance(api_key="YOUR_API_KEY")  # free key at neurvance.com
nv.download("customer-support-qa")      # done — ready for training
```

That's it. Cleaned, formatted, and ready to plug straight into your fine-tuning script.

---

## Demo

> GIF coming soon — CLI walkthrough showing dataset search, download, and inspection.

---

## Why Neurvance?

Most fine-tuning projects waste 70%+ of engineering time on data prep. Cleaning messy scrapes, reformatting, deduplicating — before you've written a single line of training code.

Neurvance flips that. Every dataset is:

- **Pre-cleaned** — no junk rows, no encoding issues
- **CC0 licensed** — no legal headaches for commercial use
- **Ready-formatted** — JSONL out of the box, compatible with most fine-tuning frameworks

---

## API Access

Free manual downloads, always. API key for bulk or incremental access.

| Plan | Access | Datasets |
|------|--------|----------|
| Free | Manual download | All 12,500+ |
| API | Bulk + incremental | All 12,500+ |

Get your key at **[neurvance.com](https://neurvance.com)**

---

## Example Fine-Tuning

See `examples/` for a full notebook: fine-tune Mistral or Llama 3 on a Neurvance dataset using Unsloth in under 10 minutes.

---

## Dataset Catalog

See [DATASETS.md](DATASETS.md) for the full searchable list organized by domain.

---

## Contributing

Want a dataset that's not here? Open a GitHub Issue or start a Discussion — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT + Commons Clause. Free for personal and commercial AI training use. You cannot sell the software itself.
