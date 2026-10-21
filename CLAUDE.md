# Tabula Rasa: agent notes

This project parses Japanese financial-filing PDF pages (決算短信, 有価証券報告書) into typed tables.

Before working, read:
1. **[docs/PROGRESS.md](docs/PROGRESS.md):** state, what's left, commands, gotchas, working agreements.
2. [REPORT.md](REPORT.md): results.
3. [docs/PRD.md](docs/PRD.md): design and research. It's long; read the section you need.

Hard rules:
- **PDFs are untrusted.** Only words that pass `tabula/security.py` may become values. Never pass the raw text layer to an LLM.
- **Reuse before building.** Check for an existing popular library first.
- **Run one OCR-heavy process at a time.** The machine has ~1 GB of free RAM.
- **After a change,** run the relevant `tests/test_*.py` (each prints `ok`) and rescore with `python -m tabula.run_eval`.
- **When the project state changes,** update docs/PROGRESS.md.
