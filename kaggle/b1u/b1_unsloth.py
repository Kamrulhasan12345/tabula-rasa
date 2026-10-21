# B1 (Unsloth): Qwen3-VL-8B-Instruct 4-bit via Unsloth, zero-shot, page image only, Kaggle T4 x2.
# One worker per GPU, pages split between them. Greedy decoding. Same prompt as kaggle/b1/b1_qwen3vl.py.
# Output: /kaggle/working/b1_outputs.json = {page_png: {"text", "secs", "tokens", "input_tokens"}}
import glob, json, os, subprocess, sys, time

if len(sys.argv) == 1:  # launcher
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "unsloth"], check=True)
    procs = [subprocess.Popen([sys.executable, __file__, str(i)], env=os.environ | {"CUDA_VISIBLE_DEVICES": str(i)}) for i in (0, 1)]
    assert all(p.wait() == 0 for p in procs), "worker failed"
    out = {}
    for i in (0, 1):
        out |= json.load(open(f"/kaggle/working/part{i}.json"))
    json.dump(out, open("/kaggle/working/b1_outputs.json", "w"), ensure_ascii=False, indent=1)
    print(len(out), "pages done")
    sys.exit()

from unsloth import FastVisionModel  # noqa: E402  (import after CUDA_VISIBLE_DEVICES is set)
import torch  # noqa: E402
from PIL import Image  # noqa: E402

PROMPT = """This image is one page of a Japanese financial statement (e.g. 連結貸借対照表).
Transcribe the table into JSON. Rules:
- Copy every cell EXACTLY as printed. Keep △ / ▲ (negative), － (nil), commas and full-width characters. Do not convert numbers.
- One entry per printed row, top to bottom, including section heading rows that have no numbers (e.g. 資産の部, 流動資産).
- "path": the section headings this row sits under, outermost first (e.g. ["資産の部", "流動資産"]). For 減価償却累計額, include the asset row it belongs to.
- "values": one string per value column, left to right; "" for an empty cell.
- "unit": the unit text printed above the table (e.g. 千円).
Output only JSON, no markdown:
{"title": "...", "unit": "...", "columns": [{"label": "...", "date": "..."}], "rows": [{"label": "...", "path": ["..."], "values": ["...", "..."]}]}"""

shard = int(sys.argv[1])
pngs = sorted(glob.glob("/kaggle/input/**/*.png", recursive=True))[shard::2]
model, tok = FastVisionModel.from_pretrained("unsloth/Qwen3-VL-8B-Instruct-unsloth-bnb-4bit", load_in_4bit=True)
FastVisionModel.for_inference(model)
out = {}
for png in pngs:
    msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": PROMPT}]}]
    text = tok.apply_chat_template(msgs, add_generation_prompt=True)
    inputs = tok(Image.open(png).convert("RGB"), text, add_special_tokens=False, return_tensors="pt").to("cuda")
    t0 = time.time()
    with torch.no_grad():
        ids = model.generate(**inputs, max_new_tokens=6000, do_sample=False, use_cache=True)
    gen = ids[0, inputs["input_ids"].shape[1]:]
    out[os.path.basename(png)] = {"text": tok.decode(gen, skip_special_tokens=True), "secs": time.time() - t0,
                                  "tokens": int(gen.shape[0]), "input_tokens": int(inputs["input_ids"].shape[1])}
    print(shard, os.path.basename(png), round(out[os.path.basename(png)]["secs"]), "s", out[os.path.basename(png)]["tokens"], "tokens", flush=True)
    json.dump(out, open(f"/kaggle/working/part{shard}.json", "w"), ensure_ascii=False)
