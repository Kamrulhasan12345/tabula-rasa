# B1 baseline: Qwen3-VL-8B-Instruct, zero-shot, page image only (no text layer), Kaggle T4 x2.
# Output: /kaggle/working/b1_outputs.json = {page_png: {"text": raw model output, "secs": float, "tokens": int}}
import subprocess, sys

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "transformers>=4.57", "accelerate"], check=True)

import glob, json, os, time
import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL = "Qwen/Qwen3-VL-8B-Instruct"
PROMPT = """This image is one page of a Japanese financial statement (e.g. 連結貸借対照表).
Transcribe the table into JSON. Rules:
- Copy every cell EXACTLY as printed. Keep △ / ▲ (negative), － (nil), commas and full-width characters. Do not convert numbers.
- One entry per printed row, top to bottom, including section heading rows that have no numbers (e.g. 資産の部, 流動資産).
- "path": the section headings this row sits under, outermost first (e.g. ["資産の部", "流動資産"]). For 減価償却累計額, include the asset row it belongs to.
- "values": one string per value column, left to right; "" for an empty cell.
- "unit": the unit text printed above the table (e.g. 千円).
Output only JSON, no markdown:
{"title": "...", "unit": "...", "columns": [{"label": "...", "date": "..."}], "rows": [{"label": "...", "path": ["..."], "values": ["...", "..."]}]}"""

assert torch.cuda.device_count() == 2, "needs GPU T4 x2"
proc = AutoProcessor.from_pretrained(MODEL)
model = AutoModelForImageTextToText.from_pretrained(
    MODEL, torch_dtype=torch.float16, device_map="balanced",
    max_memory={0: "12GiB", 1: "12GiB"}, attn_implementation="sdpa",
).eval()

out = {}
for png in sorted(glob.glob("/kaggle/input/**/*.png", recursive=True)):
    msgs = [{"role": "user", "content": [{"type": "image", "image": Image.open(png).convert("RGB")}, {"type": "text", "text": PROMPT}]}]
    inputs = proc.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt").to(model.device)
    t0 = time.time()
    with torch.no_grad():
        ids = model.generate(**inputs, max_new_tokens=6000, do_sample=False)
    gen = ids[0, inputs["input_ids"].shape[1]:]
    out[os.path.basename(png)] = {"text": proc.decode(gen, skip_special_tokens=True), "secs": time.time() - t0,
                                  "tokens": int(gen.shape[0]), "input_tokens": int(inputs["input_ids"].shape[1])}
    print(png, out[os.path.basename(png)]["secs"], "s", out[os.path.basename(png)]["tokens"], "tokens", flush=True)
    json.dump(out, open("/kaggle/working/b1_outputs.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False)[:3000])
