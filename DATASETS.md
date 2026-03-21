# Dataset Catalog

**12,500+ CC0 datasets, ready for AI fine-tuning.**

All datasets are pre-cleaned, JSONL-formatted, and free to download manually.  
API key required for bulk or incremental access — get one at [neurvance.com](https://neurvance.com).

Use `Ctrl+F` to search this page by domain or keyword.

---

## 🗣️ Conversational / Chat

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| customer-support-qa | Customer support Q&A pairs across 20 industries | 180k rows | JSONL |
| multi-turn-chat-general | General multi-turn conversations | 95k rows | JSONL |
| helpdesk-tickets | IT helpdesk ticket resolutions | 42k rows | JSONL |
| chatbot-intents | Intent classification with example utterances | 28k rows | JSONL |
| ecommerce-support | E-commerce support conversations | 67k rows | JSONL |

---

## 📝 Instruction Following

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| alpaca-style-instructions | Instruction-input-output triples, diverse tasks | 200k rows | JSONL |
| code-instructions-python | Python coding instructions with solutions | 85k rows | JSONL |
| code-instructions-js | JavaScript coding instructions with solutions | 74k rows | JSONL |
| writing-instructions | Creative and professional writing instructions | 55k rows | JSONL |
| math-instructions | Step-by-step math problem solving | 120k rows | JSONL |
| reasoning-cot | Chain-of-thought reasoning examples | 90k rows | JSONL |

---

## 🏥 Medical / Healthcare

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| medical-qa-general | General medical Q&A (not a substitute for professional advice) | 310k rows | JSONL |
| clinical-notes-summarization | Clinical note to summary pairs | 48k rows | JSONL |
| drug-interactions | Drug interaction Q&A | 22k rows | JSONL |
| symptom-checker-dialogues | Symptom assessment conversations | 38k rows | JSONL |

---

## ⚖️ Legal

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| legal-qa-us | US law Q&A pairs | 95k rows | JSONL |
| contract-clauses | Contract clause classification and explanation | 41k rows | JSONL |
| court-case-summaries | Court case summaries with outcomes | 67k rows | JSONL |
| legal-definitions | Legal term definitions and examples | 18k rows | JSONL |

---

## 💰 Finance

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| financial-qa | Financial literacy Q&A | 78k rows | JSONL |
| earnings-call-summaries | Earnings call transcript summaries | 34k rows | JSONL |
| market-sentiment | News headline to sentiment pairs | 120k rows | JSONL |
| accounting-qa | Accounting and bookkeeping Q&A | 29k rows | JSONL |

---

## 💻 Code

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| code-review-comments | Code snippet + review comment pairs | 88k rows | JSONL |
| bug-fix-pairs | Buggy code to fixed code pairs | 112k rows | JSONL |
| code-explanations | Code snippet + plain English explanation | 76k rows | JSONL |
| sql-queries | Natural language to SQL pairs | 94k rows | JSONL |
| regex-generation | Natural language to regex pairs | 18k rows | JSONL |
| api-usage-examples | API documentation + usage example pairs | 45k rows | JSONL |

---

## 📚 Education

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| k12-tutoring | K-12 tutoring dialogues across subjects | 145k rows | JSONL |
| university-exam-qa | University-level exam Q&A by subject | 200k rows | JSONL |
| flashcard-generation | Text to flashcard Q&A pairs | 88k rows | JSONL |
| essay-feedback | Student essay + feedback pairs | 42k rows | JSONL |

---

## 🌍 Multilingual

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| translation-pairs-en-es | English ↔ Spanish translation pairs | 500k rows | JSONL |
| translation-pairs-en-fr | English ↔ French translation pairs | 480k rows | JSONL |
| translation-pairs-en-de | English ↔ German translation pairs | 460k rows | JSONL |
| multilingual-instructions | Instructions in 12 languages | 180k rows | JSONL |

---

## ✍️ Creative Writing

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| story-prompts-completions | Story prompt + completion pairs | 95k rows | JSONL |
| dialogue-writing | Character dialogue generation examples | 67k rows | JSONL |
| poetry-generation | Poetry prompt + poem pairs | 38k rows | JSONL |
| product-descriptions | Product + marketing description pairs | 120k rows | JSONL |
| email-generation | Email context + draft pairs | 85k rows | JSONL |

---

## 🔬 Science & Research

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| arxiv-abstracts-summarized | ArXiv abstract + plain language summary | 210k rows | JSONL |
| science-qa-general | General science Q&A | 180k rows | JSONL |
| biology-qa | Biology concepts Q&A | 72k rows | JSONL |
| chemistry-qa | Chemistry concepts and problems | 68k rows | JSONL |
| physics-qa | Physics concepts and problems | 74k rows | JSONL |

---

## 🔒 Safety / Alignment

| Dataset | Description | Size | Format |
|---------|-------------|------|--------|
| harmless-refusals | Harmful request + safe refusal pairs | 45k rows | JSONL |
| preference-pairs | Chosen vs rejected response pairs | 120k rows | JSONL |
| red-teaming-safe | Red team prompts + safe completions | 38k rows | JSONL |

---

## 📦 Downloading a Dataset

```python
from neurvance import Neurvance

nv = Neurvance(api_key="YOUR_API_KEY")
nv.download("customer-support-qa")  # saves as customer-support-qa.jsonl
```

Or download free manually at **[neurvance.com/datasets](https://neurvance.com/datasets)**

---

## Missing something?

Open a [GitHub Issue](https://github.com/skech12/Downloader/issues/new) to request a dataset.  
Full community process in [CONTRIBUTING.md](CONTRIBUTING.md).
