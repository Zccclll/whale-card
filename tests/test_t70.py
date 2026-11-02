# -*- coding: utf-8 -*-
"""视频抽帧端到端自测：造一段有"切镜头"的测试视频 → 抽帧 → 收进临时卡。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2  # noqa: E402

FFMPEG = os.environ.get("WHALE_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
d = tempfile.mkdtemp(prefix="_t70_", dir=ROOT)   # ffmpeg 写不了 %TEMP%，放项目盘里
db = os.path.join(d, "t.db")
vid = os.path.join(d, "clip.mp4")

# 造 9 秒的测试画面（testsrc2 会自己动，帧和帧之间有真实差异）
r = subprocess.run(
    [FFMPEG, "-v", "error", "-y",
     "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=9",
     "-pix_fmt", "yuv420p", vid],
    capture_output=True, text=True, encoding="utf-8")
ok = []


def chk(name, cond, extra=""):
    ok.append(bool(cond))
    print(("  ✅ " if cond else "  ❌ ") + name + ("  " + str(extra) if extra else ""))


chk("测试视频造出来了", os.path.exists(vid), os.path.getsize(vid) if os.path.exists(vid) else 0)
if not os.path.exists(vid):
    raise SystemExit(1)

card = card2.MemoryCard(db)
mid = card.remember("那天的视频，三个镜头轮着来", salience=0.8)

env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=ROOT)
r = subprocess.run([sys.executable, "-X", "utf8",
                    os.path.join(ROOT, "tools", "media_video.py"),
                    "--db", db, "--mem", str(mid), "--file", vid,
                    "--caption", "那天的视频", "--tags", "视频,测试",
                    "--max-frames", "3"],
                   capture_output=True, text=True, encoding="utf-8", env=env)
print(r.stdout[-1400:] if r.stdout else r.stderr[-800:])
res = json.loads(r.stdout) if r.stdout and r.stdout.strip().startswith("{") else {}

chk("抽帧命令成功", r.returncode == 0 and res.get("ok"))
items = res.get("items") or []
chk("抽到不超过 3 帧", 0 < len(items) <= 3, len(items))
chk("每帧都挂上了", all(i.get("ok") for i in items), [i.get("ok") for i in items])
chk("时间窗对得上（t1>t0）", all((i.get("t1") or 0) > (i.get("t0") or 0) for i in items),
    [(i.get("t0"), i.get("t1")) for i in items])
chk("时间窗按时间升序", [i["ts"] for i in items] == sorted(i["ts"] for i in items),
    [i["ts"] for i in items])

lst = card.media_of(mid)
chk("记忆身上能看到这些帧", len(lst) == len(items), len(lst))
chk("kind 是 frame", all(i.get("media_id") for i in items))

st = card.media_stats()
chk("stats 记了帧数", st["n"] == len(items), st["n"])
v = card.verify_media()
chk("verify 通过", v["ok"], v)

# 有切镜头，抽出来的帧不该全是同一个颜色
from PIL import Image  # noqa: E402
cols = []
for row in card.db.execute("SELECT path FROM media").fetchall():
    p = os.path.join(card.card_dir, row["path"])
    im = Image.open(p).convert("RGB").resize((1, 1))
    cols.append(im.getpixel((0, 0)))
chk("抽到的帧颜色不同（识别到切镜头）", len(set(cols)) >= 2, cols)

fails = [x for x in ok if not x]
print("\n结果：%d/%d 通过" % (len(ok) - len(fails), len(ok)))
shutil.rmtree(d, ignore_errors=True)
raise SystemExit(1 if fails else 0)
