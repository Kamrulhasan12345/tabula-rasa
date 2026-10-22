"""Record the demo video + screenshots against a LIVE server (real API calls, not demo replay).

    TABULA_CACHE=runs/live_cache TABULA_STORE=runs/live_store .venv/bin/uvicorn tabula.server:app --port 8765
    .venv/bin/python tools/record_demo.py                # -> docs/media/demo.mp4, demo.gif, live-*.png
Dev-only dependency: playwright (`pip install playwright`), driving the system Chromium (no browser download).
Frames come from Chromium's own screencast (CDP) and are encoded with the system ffmpeg.
Waits for parsing are logged and fast-forwarded 12x in the final video, so it shows the real run, shorter.
"""
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"
OUT = ROOT / "docs/media"
RAW = OUT / "raw"
CHROMIUM = "/usr/bin/chromium"
SIZE = {"width": 1280, "height": 800}
BALANCE = ROOT / "docs/balance_sheet_after_correction.pdf"
SCAN = ROOT / "data/tdnet/081220260929541752/scan_p12.pdf"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    waits = []  # (start, end) seconds since recording began: parsing, fast-forwarded later
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        ctx = b.new_context(viewport=SIZE, device_scale_factor=1)
        pg = ctx.new_page()
        frames = []  # (wall time, jpeg path)
        cdp = ctx.new_cdp_session(pg)

        def on_frame(e):
            f = RAW / f"f{len(frames):05d}.jpg"
            f.write_bytes(base64.b64decode(e["data"]))
            frames.append((e["metadata"]["timestamp"], f))
            cdp.send("Page.screencastFrameAck", {"sessionId": e["sessionId"]})
        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 85, "maxWidth": SIZE["width"], "maxHeight": SIZE["height"]})
        now = time.time
        pause = lambda s: pg.wait_for_timeout(int(s * 1000))

        def parse_and_wait():
            pg.click("#parse")
            start = now()
            pg.wait_for_function("() => /· (queued|running)/.test(document.getElementById('state').textContent)", timeout=30_000)
            pg.wait_for_function("() => /· (done|failed)/.test(document.getElementById('state').textContent)", timeout=600_000)
            waits.append((start + 1.0, now()))  # keep the first second of "running" visible
            pg.wait_for_selector("#pagebox img", timeout=60_000)
            pg.wait_for_function("() => document.querySelector('#pagebox img').complete", timeout=60_000)
            pause(1.5)

        def upload(path):
            pg.set_input_files("#file", str(path))
            pause(0.6)
            pg.click("#upload")
            pg.wait_for_function("() => !document.getElementById('parse').disabled", timeout=120_000)
            pause(0.8)

        # 1. born-digital balance sheet, live TypeLLM cross-check
        pg.goto(BASE + "/")
        pg.wait_for_function("() => document.getElementById('reader').options.length > 0")
        pg.evaluate("() => { try { localStorage.clear() } catch {} }")
        pg.select_option("#reader", "typellm")
        pg.uncheck("#demo")
        pause(2.0)
        upload(BALANCE)
        parse_and_wait()
        pg.screenshot(path=str(OUT / "live-balance.png"))
        box = pg.locator(".box").nth(30)  # a value deep in the table: highlights the row on both sides
        box.scroll_into_view_if_needed()
        box.click()
        pause(2.5)
        pg.screenshot(path=str(OUT / "live-balance-click.png"))
        pg.mouse.wheel(0, 500)
        pause(1.5)

        # 2. scanned page: no text layer, read from the image only (amber)
        pg.evaluate("() => window.scrollTo(0, 0)")
        upload(SCAN)
        parse_and_wait()
        pause(1.5)
        pg.screenshot(path=str(OUT / "live-scan.png"))

        # 3. switch the reader to ScaleDown and re-parse the balance sheet: disagreements turn red
        pg.evaluate("() => window.scrollTo(0, 0)")
        pg.select_option("#reader", "scaledown")
        pause(1.5)
        upload(BALANCE)
        parse_and_wait()
        pause(1.0)
        pg.screenshot(path=str(OUT / "live-scaledown.png"))
        red = pg.locator(".box.review")
        if red.count():
            red.first.scroll_into_view_if_needed()
            red.first.click()
            pause(2.5)
        end = now()
        cdp.send("Page.stopScreencast")
        ctx.close()
        b.close()
    (RAW / "waits.json").write_text(json.dumps({"waits": waits, "end": end, "frames": len(frames)}))
    encode(frames, waits, end)


def encode(frames, waits, end, speed=12):
    """Screencast frames arrive only on change: each frame lasts until the next. Durations inside parse waits are
    divided by `speed`. Then demo.mp4 (H.264) and demo.gif."""
    lst = RAW / "frames.txt"
    lines = []
    for i, (t, f) in enumerate(frames):
        nxt = frames[i + 1][0] if i + 1 < len(frames) else end
        d = nxt - t
        if any(a <= t < z for a, z in waits):
            d /= speed
        lines += [f"file '{f.name}'", f"duration {max(d, 0.02):.3f}"]
    lines.append(f"file '{frames[-1][1].name}'")  # concat demuxer: the last file is listed twice
    lst.write_text("\n".join(lines) + "\n")
    mp4 = OUT / "demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", "fps=25,scale=1280:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24",
                    "-movflags", "+faststart", str(mp4)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf",
                    "fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer",
                    str(OUT / "demo.gif")], check=True)
    print("wrote", mp4, OUT / "demo.gif")


if __name__ == "__main__":
    main()
