# -*- coding: utf-8 -*-
"""把一张图收进卡里（宿主侧）。

为什么不在卡里做：卡是单文件 SQLite + 一盒 python，它只该记「这张帧属于谁」。
解码、缩放、转 WebP 这些活由宿主做 —— 谁有 Pillow 谁来干，卡不背这个依赖。

用法：
    python tools/media_import.py --mem 123 --caption "路边摊吃烧烤" \\
        --tags 烧烤,同学 --file D:\\pics\\a.jpg

    # 一次收多张（同一句话，挂同一条记忆）
    python tools/media_import.py --mem 123 --caption "那天的照片" \\
        --file a.jpg b.jpg c.jpg

    # 只看会做什么，不写库
    python tools/media_import.py --mem 123 --file a.jpg --dry-run

存的永远是**派生帧**：长边 800px、WebP q80（60–90 KB）+ 96px 缩略图。
原图不搬家、不进仓库、不参与任何逻辑 —— 我们要的是"画面"这件事，
不是那份原始字节；真要看原图，用户手里本来就有。
"""

import argparse
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_DB = os.environ.get("WHALE_DB") or os.path.join(ROOT, "card.db")


def _load_pil():
    try:
        from PIL import Image
        return Image
    except Exception as e:
        raise SystemExit("需要 Pillow：pip install pillow（%s）" % e)


def render_frame(path, max_edge=800, thumb_edge=96, quality=80):
    """把任意图变成「一帧」：长边不超 800 的 WebP + 96px 缩略图。

    返回 (main_bytes, thumb_bytes, w, h)。EXIF 旋转在这里就摆正 ——
    手机拍的图十张有八张是躺着的，存进去之前不摆正，以后看就是歪的。
    """
    Image = _load_pil()
    from PIL import ImageOps
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    w, h = im.size
    scale = min(1.0, float(max_edge) / max(1, max(w, h)))
    if scale < 1.0:
        im2 = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                        Image.LANCZOS)
    else:
        im2 = im
    buf = io.BytesIO()
    im2.save(buf, format="WEBP", quality=quality, method=4)
    main = buf.getvalue()
    im3 = im2.copy()
    im3.thumbnail((thumb_edge, thumb_edge), Image.LANCZOS)
    tb = io.BytesIO()
    im3.save(tb, format="WEBP", quality=70, method=4)
    return main, tb.getvalue(), im2.size[0], im2.size[1]


def save_frame(card, sha, main, thumb):
    """写进 media/<前两位>/<sha>.webp，返回相对卡目录的路径。"""
    root = card.media_root()
    sub = os.path.join("media", sha[:2])
    d = os.path.join(card.card_dir, sub)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, sha + ".webp")
    with open(p, "wb") as f:
        f.write(main)
    pt = os.path.join(d, sha + "_t.webp")
    with open(pt, "wb") as f:
        f.write(thumb)
    return (os.path.join(sub, sha + ".webp"),
            os.path.join(sub, sha + "_t.webp"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="把图片收进卡里的画面记忆")
    ap.add_argument("--mem", type=int, required=True, help="挂到哪条记忆（mem.id）")
    ap.add_argument("--file", nargs="+", required=True, help="源图（可多张）")
    ap.add_argument("--caption", default="", help="当时那句话（会跟着画面一起被忘）")
    ap.add_argument("--tags", default="", help="逗号分隔的实体标签：烧烤,同学")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--pinned", action="store_true", help="锁定画面，永不自动删")
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--dry-run", action="store_true", help="只算，不写库不落盘")
    a = ap.parse_args(argv)

    import card2
    card = card2.MemoryCard(a.db)
    tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()]
    out = []
    for path in a.file:
        if not os.path.exists(path):
            out.append({"file": path, "ok": False, "why": "文件不存在"})
            continue
        try:
            main, thumb, w, h = render_frame(path, card2.MEDIA_IMG_MAX_EDGE,
                                             card2.MEDIA_THUMB_EDGE, a.quality)
        except Exception as e:
            out.append({"file": path, "ok": False, "why": "解码失败：%s" % e})
            continue
        sha = hashlib.sha256(main).hexdigest()
        rec = {"file": os.path.basename(path), "sha256": sha[:12],
               "size": "%dx%d" % (w, h), "kb": round(len(main) / 1024.0, 1)}
        if a.dry_run:
            rec["ok"] = True
            rec["dry"] = True
            out.append(rec)
            continue
        rel, rel_t = save_frame(card, sha, main, thumb)
        r = card.attach_media(a.mem, sha, caption=a.caption, tags=tags,
                              kind="image", mime="image/webp", path=rel, thumb=rel_t,
                              w=w, h=h, nbytes=len(main),
                              source="user", pinned=1 if a.pinned else 0)
        rec.update(r)
        rec["path"] = rel
        out.append(rec)

    okn = len([r for r in out if r.get("ok")])
    print(json.dumps({"ok": okn == len(out), "mem": a.mem,
                      "caption": a.caption, "tags": tags,
                      "n": len(out), "ok_n": okn, "items": out},
                     ensure_ascii=False, indent=1))
    return 0 if okn else 1


if __name__ == "__main__":
    raise SystemExit(main())
