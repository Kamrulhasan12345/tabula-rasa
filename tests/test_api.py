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
os.environ["TABULA_VLM_URL"] = f"http://127.0.0.1:{srv.server_port}/v1"
os.environ["TABULA_STORE"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient  # noqa: E402
from tabula.server import app  # noqa: E402

c = TestClient(app)
assert c.get("/health").json()["vlm_mode"] == "live"
assert c.post("/documents", files={"file": ("x.pdf", b"hello")}).status_code == 415
assert c.get("/documents/../../etc").status_code == 404

pdf = (ROOT / "docs/balance_sheet_after_correction.pdf").read_bytes()
meta = c.post("/documents", files={"file": ("balance_sheet_after_correction.pdf", pdf, "application/pdf")}).json()
assert meta["table_pages"] == [1] and meta["state"] == "uploaded"
doc_id = meta["id"]
assert c.post(f"/documents/{doc_id}/parse?pages=9").status_code == 422
assert c.post(f"/documents/{doc_id}/parse").status_code == 202  # TestClient runs the background task before returning
meta = c.get(f"/documents/{doc_id}").json()
assert meta["state"] == "done" and meta["pages_done"] == [1], meta

assert seen and seen[0]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
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
print("ok", len(page["check_results"]), "sum checks")
