"""Track B/C adapters against one fake server that checks each request has the documented shape and answers
with a canned reply (the sample page's saved VLM transcription). No network, no keys, no OCR."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from tabula import remote  # noqa: E402
from tabula.b1 import _json, convert  # noqa: E402
from tabula.vlm import _saved  # noqa: E402

for k in ("SCALEDOWN_API_KEY", "TYPELLM_API_KEY", "SQWISH_API_KEY", "TABULA_AMOS"):  # .env keys: never call real APIs here
    os.environ.pop(k, None)
RAW = _json(_saved()["balance_sheet_after_correction_p1.png"]["text"])
TABLE = {"unit": RAW["unit"], "columns": RAW["columns"],
         "rows": [{"label": r["label"], "path": " / ".join(r["path"]), "values": r["values"]} for r in RAW["rows"]]}
PNG = b"\x89PNG fake"
seen = {}


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen[self.path] = (body, {k.lower(): v for k, v in self.headers.items()})
        if self.path == "/v1/chat/completions":  # ScaleDown
            assert body["model"] == "extract-1" and body["document_mime_type"] == "image/png" and body["document"]
            assert body["response_format"]["type"] == "json_schema"
            out = {"choices": [{"message": {"content": json.dumps({"entities": [], "structured_result": TABLE})}}]}
        elif self.path == "/v1/generate":  # TypeLLM
            assert body["images"][0].startswith("data:image/png;base64,") and body["questions"]["rows"]["type"] == "array"
            out = {"result": TABLE, "status": "completed"}
        elif self.path == "/v1/systemone":  # Amos
            assert body["images"] and all(q["type"] == "noul" for q in body["questions"].values())
            out = {"answers": {k: {"type": "noul", "noul": 0.97} for k in body["questions"]}}
        elif self.path == "/v1/decide":  # Sqwish
            d = body["decisions"]
            assert len(d) <= 128 and d[0]["kind"] == "single" and set(d[0]["outcomes"]) == set(remote.ROW_TYPES)
            out = {"decisions": {x["id"]: {"top": "item", "p_top": 0.4 if i == 0 else 0.9,
                                           "policy": {"action": "abstain" if i == 0 else "item"}} for i, x in enumerate(d)}}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}/v1"

assert remote.scaledown_read(PNG) is None and remote.amos_verify(PNG, [("k", "x", 0, "1")]) is None  # no keys: off
os.environ |= {"SCALEDOWN_API_KEY": "k1", "TYPELLM_API_KEY": "k2", "SQWISH_API_KEY": "k3", "TABULA_AMOS": "1",
               "TABULA_SCALEDOWN_URL": base, "TABULA_TYPELLM_URL": base, "TABULA_AMOS_URL": base, "TABULA_SQWISH_URL": base}

ref = convert(RAW, "s", 1)
for name, read in remote.READERS.items():
    doc = convert(read(PNG), "s", 1)
    assert [r["values"] for r in doc["rows"]] == [r["values"] for r in ref["rows"]], name
    assert [r["path"] for r in doc["rows"]] == [r["path"] for r in ref["rows"]], name
assert seen["/v1/chat/completions"][1]["x-api-key"] == "k1" and seen["/v1/generate"][1]["authorization"] == "Bearer k2"

probs = remote.amos_verify(PNG, [(("r01", f"c{i}"), "現金及び預金", i, "1") for i in range(40)])  # 2 batches
assert len(probs) == 40 and set(probs.values()) == {0.97}

rows = [{"id": f"r{i:03d}", "label": "その他", "path": [], "raw": {"c0": "1"}} for i in range(130)]  # 2 batches
kinds = remote.sqwish_row_types(rows)
assert len(kinds) == 130 and kinds["r000"] == (None, 0.4) and kinds["r001"] == ("item", 0.9)
print("ok")
