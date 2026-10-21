"""HTTP API for a UI. Upload a PDF, start parsing (takes minutes with a live VLM), poll, fetch pages.

    .venv/bin/uvicorn tabula.server:app --port 8000          # docs at http://localhost:8000/docs

POST /documents                      multipart file (+ optional form field "name": key for saved VLM outputs)
POST /documents/{id}/parse?pages=3,5 start parsing in the background (default: auto-detected pages) -> 202
GET  /documents/{id}                 state: uploaded | queued | running | done | failed, pages_done
GET  /documents/{id}/pages/{n}       PRD §4 JSON with cell_status, cell_bbox, check_results
GET  /documents/{id}/pages/{n}.csv | .html
GET  /documents/{id}/pages/{n}/image.png?dpi=100   page render; bbox px = points * dpi / 72
GET  /health
Env: TABULA_VLM_URL / _MODEL / _KEY (tabula/vlm.py), TABULA_STORE (upload dir), TABULA_CORS_ORIGINS (UI origins).
Uploads are untrusted: only "%PDF" files up to 50 MB; ids are content hashes, names are sanitized.
"""
import hashlib
import io
import json
import os
import re
import threading
from pathlib import Path

import pdfplumber
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response

from tabula import vlm
from tabula.api import find_pages, parse_pdf, to_csv, to_html

STORE = Path(os.environ.get("TABULA_STORE", Path(__file__).parent.parent / "runs/store"))
MAX_BYTES = 50 * 1024 * 1024
LOCK = threading.Lock()  # ponytail: one parse at a time in-process (OCR needs ~600 MB); a job queue + workers if load grows

app = FastAPI(title="Tabula Rasa", description="Japanese financial-filing PDF pages -> verified tables")
if os.environ.get("TABULA_CORS_ORIGINS"):  # comma-separated UI origins, e.g. http://localhost:5173
    app.add_middleware(CORSMiddleware, allow_origins=os.environ["TABULA_CORS_ORIGINS"].split(","), allow_methods=["*"], allow_headers=["*"])


def _dir(doc_id):
    if not re.fullmatch(r"[0-9a-f]{16}", doc_id):
        raise HTTPException(404, "unknown document")
    d = STORE / doc_id
    if not (d / "meta.json").exists():
        raise HTTPException(404, "unknown document")
    return d


def _meta(d):
    return json.loads((d / "meta.json").read_text())


def _save_meta(d, meta):
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))


@app.get("/health")
def health():
    from tabula.hybrid import reader_name
    return {"status": "ok", "reader": reader_name(), "vlm_mode": vlm.mode()}


@app.post("/documents", status_code=201)
async def upload(file: UploadFile = File(...), name: str | None = Form(None)):
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "PDF over 50 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "not a PDF")
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    d = STORE / doc_id
    if (d / "meta.json").exists():
        return _meta(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / "doc.pdf").write_bytes(data)
    try:
        with pdfplumber.open(d / "doc.pdf") as pdf:
            n_pages = len(pdf.pages)
        tables, image_only = find_pages(d / "doc.pdf")
    except Exception as e:
        (d / "doc.pdf").unlink()
        d.rmdir()
        raise HTTPException(422, f"unreadable PDF: {type(e).__name__}")
    key = re.sub(r"[^A-Za-z0-9_-]", "", name or Path(file.filename or "").stem)[:64] or doc_id
    meta = {"id": doc_id, "name": key, "filename": file.filename, "n_pages": n_pages, "table_pages": tables,
            "image_only_pages": image_only, "state": "uploaded", "pages_done": [], "error": None}
    _save_meta(d, meta)
    return meta


def _run(d, pages):
    meta = _meta(d)
    with LOCK:
        meta.update(state="running", pages_done=[], error=None)
        _save_meta(d, meta)

        def done(doc):
            (d / f"p{doc['page']}.json").write_text(json.dumps(doc, ensure_ascii=False))
            meta["pages_done"].append(doc["page"])
            _save_meta(d, meta)
        try:
            res = parse_pdf(d / "doc.pdf", pages, meta["name"], on_page=done)
            meta.update(state="done", vlm_mode=res["vlm_mode"])
        except Exception as e:  # keep the pages already done; report the rest
            meta.update(state="failed", error=f"{type(e).__name__}: {e}")
        _save_meta(d, meta)


@app.post("/documents/{doc_id}/parse", status_code=202)
def start(doc_id: str, background: BackgroundTasks, pages: str | None = Query(None, pattern=r"^\d+(,\d+)*$")):
    d = _dir(doc_id)
    meta = _meta(d)
    if meta["state"] in ("queued", "running"):
        return meta
    page_list = [int(p) for p in pages.split(",")] if pages else None
    if page_list and not all(1 <= p <= meta["n_pages"] for p in page_list):
        raise HTTPException(422, f"pages must be 1..{meta['n_pages']}")
    meta["state"] = "queued"
    _save_meta(d, meta)
    background.add_task(_run, d, page_list)
    return meta


@app.get("/documents/{doc_id}")
def status(doc_id: str):
    return _meta(_dir(doc_id))


def _page(doc_id, n):
    p = _dir(doc_id) / f"p{n}.json"
    if not p.exists():
        raise HTTPException(404, "page not parsed (yet)")
    return json.loads(p.read_text())


@app.get("/documents/{doc_id}/pages/{n}.csv", response_class=PlainTextResponse)
def page_csv(doc_id: str, n: int):
    return PlainTextResponse("﻿" + to_csv(_page(doc_id, n)), media_type="text/csv; charset=utf-8")


@app.get("/documents/{doc_id}/pages/{n}.html", response_class=PlainTextResponse)
def page_html(doc_id: str, n: int):
    return PlainTextResponse(to_html(_page(doc_id, n)), media_type="text/html; charset=utf-8")


@app.get("/documents/{doc_id}/pages/{n}/image.png")
def page_image(doc_id: str, n: int, dpi: int = Query(100, ge=36, le=300)):
    d = _dir(doc_id)
    if not 1 <= n <= _meta(d)["n_pages"]:
        raise HTTPException(404, "no such page")
    with pdfplumber.open(d / "doc.pdf") as pdf:
        buf = io.BytesIO()
        pdf.pages[n - 1].to_image(resolution=dpi).original.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/documents/{doc_id}/pages/{n}")
def page_json(doc_id: str, n: int):
    return _page(doc_id, n)
