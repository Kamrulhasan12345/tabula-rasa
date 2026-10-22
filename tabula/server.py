"""HTTP API for a UI. Upload a PDF, start parsing (takes minutes with a live VLM), poll, fetch pages.

    .venv/bin/uvicorn tabula.server:app --port 8000          # docs at http://localhost:8000/docs

POST /documents                      multipart file (+ optional form field "name": key for saved VLM outputs)
POST /documents/{id}/parse           start parsing in the background -> 202. Optional JSON body (ParseOptions):
                                     pages, reader (typellm|scaledown|vlm|none), crosscheck (all|needed), demo,
                                     fallback, vlm_url, vlm_model, vlm_key, typellm_key, scaledown_key
GET  /readers                        readers, measured accuracy, availability on this server
GET  /documents/{id}                 state: uploaded | queued | running | done | failed, pages_done
GET  /documents/{id}/pages/{n}       PRD §4 JSON with cell_status, cell_bbox, check_results
GET  /documents/{id}/pages/{n}.csv | .html
GET  /documents/{id}/pages/{n}/image.png?dpi=100   page render; bbox px = points * dpi / 72
GET  /health
GET  /samples, POST /samples/{key}   eval filings on this machine, loaded with their eval pages (demo)
GET  /                               demo viewer (tabula/static/index.html)
Env: TABULA_ALLOW_CLIENT_ENDPOINTS=0 rejects request-supplied VLM URLs (shared servers: the server would call them).
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
from typing import Literal

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from tabula import vlm
from tabula.api import find_pages, parse_pdf, to_csv, to_html
from tabula.security import render

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


ROOT = Path(__file__).parent.parent


def _samples():
    """Eval filings whose PDF is on this machine: {key: (pdf path, doc_id, [eval pages])}. Their saved replies
    let TABULA_DEMO=1 run with no keys or network. In a fresh clone only the sample page is present."""
    out = {}
    for gp in sorted((ROOT / "gold").glob("*.json")):
        g = json.loads(gp.read_text())
        if g.get("gold_status") not in (None, "confirmed"):  # None: the hand-labelled sample page
            continue
        pdf = ROOT / ((g.get("source") or {}).get("pdf") or f"docs/{g['doc_id']}.pdf")
        if pdf.exists():
            title = ((g.get("source") or {}).get("title") or g["doc_id"]) + (" · synthetic scan" if "_scan" in g["doc_id"] else "")
            e = out.setdefault(g["doc_id"], {"pdf": pdf, "pages": [], "title": title,
                                             "company": (g.get("source") or {}).get("company") or ""})
            e["pages"].append(g["page"])
    return out


@app.get("/samples")
def samples():
    return [{"key": k, "title": v["title"], "company": v["company"], "pages": sorted(v["pages"])} for k, v in _samples().items()]


@app.post("/samples/{key}", status_code=201)
def load_sample(key: str):
    s = _samples().get(key)
    if not s:
        raise HTTPException(404, "no such sample on this machine")
    return _register(s["pdf"].read_bytes(), s["pdf"].name, key, sorted(s["pages"]))


@app.get("/", include_in_schema=False)
def viewer():
    return FileResponse(Path(__file__).parent / "static/index.html")


@app.get("/health")
def health():
    from tabula.hybrid import reader_name
    return {"status": "ok", "reader": reader_name(), "demo": os.environ.get("TABULA_DEMO") == "1",
            "crosscheck": os.environ.get("TABULA_CROSSCHECK", "all"), "vlm_mode": vlm.mode()}


@app.post("/documents", status_code=201)
async def upload(file: UploadFile = File(...), name: str | None = Form(None)):
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "PDF over 50 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "not a PDF")
    return _register(data, file.filename, name)


def _register(data, filename, name, demo_pages=None):
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    d = STORE / doc_id
    if (d / "meta.json").exists():
        meta = _meta(d)
        if demo_pages and meta.get("demo_pages") != demo_pages:  # same PDF loaded as a sample: key replies by its eval id
            meta.update(demo_pages=demo_pages, name=name)
            _save_meta(d, meta)
        return meta
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
    key = re.sub(r"[^A-Za-z0-9_-]", "", name or Path(filename or "").stem)[:64] or doc_id
    meta = {"id": doc_id, "name": key, "filename": filename, "n_pages": n_pages, "table_pages": tables,
            "image_only_pages": image_only, "demo_pages": demo_pages, "state": "uploaded", "pages_done": [], "error": None}
    _save_meta(d, meta)
    return meta


class ParseOptions(BaseModel):
    """Per-request choices; anything left out falls back to the server's env (.env). Keys are used for this parse
    only and never written to disk (meta.json records which options were set, not their values)."""
    pages: list[int] | None = None
    reader: Literal["typellm", "scaledown", "vlm", "none"] | None = None
    crosscheck: Literal["all", "needed"] | None = None
    demo: bool | None = None
    fallback: bool | None = None
    vlm_url: str | None = Field(None, max_length=500)
    vlm_model: str | None = Field(None, max_length=200)
    vlm_key: str | None = Field(None, max_length=500)
    typellm_key: str | None = Field(None, max_length=500)
    scaledown_key: str | None = Field(None, max_length=500)

    def settings(self):
        b = lambda v: None if v is None else ("1" if v else "0")
        return {k: v for k, v in {
            "TABULA_READER": self.reader, "TABULA_CROSSCHECK": self.crosscheck, "TABULA_DEMO": b(self.demo),
            "TABULA_FALLBACK": b(self.fallback), "TABULA_VLM_URL": self.vlm_url, "TABULA_VLM_MODEL": self.vlm_model,
            "TABULA_VLM_KEY": self.vlm_key, "TYPELLM_API_KEY": self.typellm_key, "SCALEDOWN_API_KEY": self.scaledown_key,
        }.items() if v not in (None, "")}

    def public(self):  # what's safe to store and show back
        out = self.model_dump(exclude={"vlm_key", "typellm_key", "scaledown_key"}, exclude_none=True)
        out["keys_from_request"] = [k for k in ("vlm_key", "typellm_key", "scaledown_key") if getattr(self, k)]
        return out


def _run(d, pages, settings=None):
    meta = _meta(d)
    with LOCK:
        meta.update(state="running", pages_done=[], error=None)
        _save_meta(d, meta)

        def done(doc):
            (d / f"p{doc['page']}.json").write_text(json.dumps(doc, ensure_ascii=False))
            meta["pages_done"].append(doc["page"])
            _save_meta(d, meta)
        try:
            res = parse_pdf(d / "doc.pdf", pages, meta["name"], on_page=done, settings=settings)
            meta.update(state="done", reader=res["reader"], demo=res["demo"], reader_calls=res["reader_calls"],
                        statements=res["statements"])
        except Exception as e:  # keep the pages already done; report the rest
            meta.update(state="failed", error=f"{type(e).__name__}: {e}")
        _save_meta(d, meta)


@app.post("/documents/{doc_id}/parse", status_code=202)
def start(doc_id: str, background: BackgroundTasks, opts: ParseOptions | None = Body(None),
          pages: str | None = Query(None, pattern=r"^\d+(,\d+)*$")):
    d = _dir(doc_id)
    meta = _meta(d)
    if meta["state"] in ("queued", "running"):
        return meta
    opts = opts or ParseOptions()
    page_list = opts.pages or ([int(p) for p in pages.split(",")] if pages else meta.get("demo_pages"))  # samples: eval pages
    if page_list and not all(1 <= p <= meta["n_pages"] for p in page_list):
        raise HTTPException(422, f"pages must be 1..{meta['n_pages']}")
    if opts.vlm_url:
        if os.environ.get("TABULA_ALLOW_CLIENT_ENDPOINTS", "1") != "1":
            raise HTTPException(403, "this server doesn't accept VLM endpoints from requests (TABULA_ALLOW_CLIENT_ENDPOINTS=0)")
        if not re.match(r"https?://", opts.vlm_url):
            raise HTTPException(422, "vlm_url must be http(s)")
    meta.update(state="queued", options=opts.public())
    _save_meta(d, meta)
    background.add_task(_run, d, page_list, opts.settings())
    return meta


@app.get("/readers")
def readers():
    """Every reader with its measured accuracy and whether this server can use it without request-supplied keys."""
    from tabula import config
    from tabula.hybrid import reader_name
    return {"default": reader_name(), "demo": config.get("TABULA_DEMO") == "1",
            "crosscheck": config.get("TABULA_CROSSCHECK", None, "all"),
            "client_endpoints": os.environ.get("TABULA_ALLOW_CLIENT_ENDPOINTS", "1") == "1",
            "readers": [{"id": k, **v, "available": config.available(k)} for k, v in config.READERS.items()]}


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
        render(pdf.pages[n - 1], dpi).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/documents/{doc_id}/pages/{n}")
def page_json(doc_id: str, n: int):
    return _page(doc_id, n)
