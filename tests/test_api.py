"""API end to end on the sample page (~30 s, OCR), with the VLM as a fake OpenAI-compatible endpoint that
answers with the saved Kaggle output. Exercises the live path: render -> HTTP -> convert -> hybrid -> checks."""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from tabula.vlm import _saved  # noqa: E402

SAVED = _saved()["balance_sheet_after_correction_p1.png"]["text"]
seen = []


class FakeVLM(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append(body)
        out = json.dumps({"choices": [{"message": {"content": SAVED}}], "usage": {"completion_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), FakeVLM)
threading.Thread(target=srv.serve_forever, daemon=True).start()
VLM_URL = f"http://127.0.0.1:{srv.server_port}/v1"  # given per request, not via env
os.environ.pop("TABULA_VLM_URL", None)
os.environ["TABULA_STORE"] = tempfile.mkdtemp()
os.environ["TABULA_READER"] = "vlm"  # the .env TypeLLM key would otherwise make it the default reader
for k in ("SCALEDOWN_API_KEY", "TYPELLM_API_KEY", "SQWISH_API_KEY", "TABULA_AMOS"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402
from tabula.server import app  # noqa: E402

c = TestClient(app)
assert c.get("/health").json()["vlm_mode"] == "demo"  # no server-side endpoint
cfg = c.get("/readers").json()
assert {r["id"] for r in cfg["readers"]} == {"typellm", "scaledown", "vlm", "none"} and cfg["client_endpoints"]
assert c.post("/documents", files={"file": ("x.pdf", b"hello")}).status_code == 415
assert c.get("/documents/../../etc").status_code == 404

pdf = (ROOT / "docs/balance_sheet_after_correction.pdf").read_bytes()
meta = c.post("/documents", files={"file": ("balance_sheet_after_correction.pdf", pdf, "application/pdf")}).json()
assert meta["table_pages"] == [1] and meta["state"] == "uploaded"
doc_id = meta["id"]
assert c.post(f"/documents/{doc_id}/parse?pages=9").status_code == 422
assert c.post(f"/documents/{doc_id}/parse", json={"reader": "vlm", "vlm_url": "file:///etc/passwd"}).status_code == 422
r = c.post(f"/documents/{doc_id}/parse", json={"reader": "vlm", "vlm_url": VLM_URL, "vlm_model": "m", "vlm_key": "secret",
                                               "fallback": False})
assert r.status_code == 202 and "secret" not in r.text  # TestClient runs the background task before returning
meta = c.get(f"/documents/{doc_id}").json()
assert meta["state"] == "done" and meta["pages_done"] == [1], meta

assert seen and seen[0]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,") and seen[0]["model"] == "m"
assert "secret" not in (Path(os.environ["TABULA_STORE"]) / doc_id / "meta.json").read_text()  # keys never stored
page = c.get(f"/documents/{doc_id}/pages/1").json()
st = [s for r in page["rows"] for s in r["cell_status"].values()]
assert page["engine"] == "b0+b1" and st.count("agreed") == 74 and page["status"] == "ok", (page["status"], page["warnings"])
cash = next(r for r in page["rows"] if r["label"] == "現金及び預金")
assert cash["values"] == {"c0": 2002429, "c1": 608281} and all(len(b) == 4 for b in cash["cell_bbox"].values())
assert page["check_results"] and all(r["status"] != "fail" for r in page["check_results"])
assert c.get(f"/documents/{doc_id}/pages/1.csv").text.lstrip("﻿").startswith("path,label,row_type")
assert 'class="agreed"' in c.get(f"/documents/{doc_id}/pages/1.html").text
assert c.get(f"/documents/{doc_id}/pages/1/image.png").content[:4] == b"\x89PNG"
assert c.get(f"/documents/{doc_id}/pages/2").status_code == 404
assert "Tabula Rasa" in c.get("/").text  # demo viewer
assert c.post(f"/documents/{doc_id}/parse", json={"reader": "none"}).status_code == 202  # text layer only
none = c.get(f"/documents/{doc_id}/pages/1").json()
assert none["engine"] == "b0" and {s for r in none["rows"] for s in r["cell_status"].values()} == {"b0_only"}
assert any(x["key"] == "balance_sheet_after_correction" for x in c.get("/samples").json())
assert c.post("/samples/balance_sheet_after_correction").json()["demo_pages"] == [1]
assert c.post("/samples/nope").status_code == 404
print("ok", len(page["check_results"]), "sum checks")
