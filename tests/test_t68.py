# -*- coding: utf-8 -*-
"""导入器端到端自测：造一张图 → 收进临时卡 → 校验文件/元数据/去重/出图。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

import card2  # noqa: E402

d = tempfile.mkdtemp()
db = os.path.join(d, "t.db")
card = card2.MemoryCard(db)
mid = card.remember("那天晚上在路边摊吃烧烤，还拍了照片", salience=0.85)

# 造一张 1600x1200 的"照片"（超过 800 长边，会被压）
img = Image.new("RGB", (1600, 1200), (28, 34, 44))
dr = ImageDraw.Draw(img)
dr.rectangle([200, 700, 1400, 1150], fill=(96, 66, 40))
for i in range(12):
    dr.ellipse([300 + i * 90, 760, 360 + i * 90, 820], fill=(255, 180, 90))
p1 = os.path.join(d, "bbq.jpg")
img.save(p1, quality=92)

ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
TOOL = [sys.executable, "-X", "utf8", os.path.join(ROOT, "tools", "media_import.py")]
r = subprocess.run(TOOL + ["--db", db, "--mem", str(mid),
                    "--caption", "路边摊吃烧烤，还拍了照片",
                    "--tags", "烧烤,同学", "--file", p1],
                   capture_output=True, text=True, encoding="utf-8", env=ENV)
print(r.stdout[-1200:])
res = json.loads(r.stdout)
ok = []


def chk(name, cond, extra=""):
    ok.append(bool(cond))
    print(("  ✅ " if cond else "  ❌ ") + name + ("  " + str(extra) if extra else ""))


chk("导入退出码 0", r.returncode == 0)
item = (res.get("items") or [{}])[0]
chk("库里挂上了", item.get("ok") and item.get("media_id"), {k: item.get(k) for k in ("ok", "media_id", "kb", "size")})
chk("压到 800 长边", item.get("size") == "800x600", item.get("size"))
chk("文件小于 200KB", (item.get("kb") or 999) < 200, item.get("kb"))

lst = card.media_of(mid)
chk("记忆身上能看到这张", len(lst) == 1 and lst[0]["id"] == item.get("media_id"), lst)
chk("权重是满的（刚挂上）", abs(lst[0]["w"] - 1.0) < 0.01, lst[0]["w"])

p = os.path.join(card.card_dir, item["path"])
t = os.path.join(card.card_dir, item["path"].replace(".webp", "_t.webp"))
chk("主帧文件在", os.path.exists(p), p)
chk("缩略图在", os.path.exists(t))

# 同一张图再导一次 → 复用，不新增行
r2 = subprocess.run(TOOL + ["--db", db, "--mem", str(mid), "--caption", "又导一次",
                     "--file", p1],
                    capture_output=True, text=True, encoding="utf-8", env=ENV)
res2 = json.loads(r2.stdout)
chk("同一张图重导 → 复用（dup=True）", res2["items"][0].get("dup") is True,
    res2["items"][0].get("media_id"))
chk("库里只有一行 media", card.db.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1)

v = card.verify_media()
chk("verify 通过", v["ok"], v)
st = card.media_stats()
chk("stats 有帧、有字节", st["n"] == 1 and st["bytes"] > 0,
    {k: st[k] for k in ("n", "bytes", "mb")})

subs = [x for x in ok if not x]
print("\n结果：%d/%d 通过" % (len(ok) - len(subs), len(ok)))
shutil.rmtree(d, ignore_errors=True)
raise SystemExit(1 if subs else 0)
