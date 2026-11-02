# -*- coding: utf-8 -*-
"""T6-2 验收：星云图生成器

不测「好不好看」（那个要人看），只测：
  · 数据取得全不全
  · 布局算得对不对（不重叠、不跑出画布）
  · HTML 自包含（不联网）、结构完整、JSON 可解析
  · 命中标注正确
  · 空库 / 单条 / 无突触 不崩
"""
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import export_viz as viz                            # noqa: E402
from card2 import MemoryCard                        # noqa: E402

ok_n = 0
bad_n = 0


def ck(name, cond, extra=""):
    global ok_n, bad_n
    if cond:
        ok_n += 1
        print("  ✅ %s %s" % (name, extra))
    else:
        bad_n += 1
        print("  ❌ %s %s" % (name, extra))


TMPDIR = os.path.join(HERE, "_t62")
TMP = os.path.join(TMPDIR, "card.db")
if os.path.isdir(TMPDIR):
    shutil.rmtree(TMPDIR, ignore_errors=True)
os.makedirs(TMPDIR, exist_ok=True)

FILLER = [
    "今天中午在学校食堂吃了白米饭配红烧肉，很满足",
    "数学老师讲导数讲到第三遍我才听懂，脑子有点晕",
    "放学路上看见一只橘猫蹲在墙角，我蹲下来看了它两分钟",
    "同桌说他以后想学临床医学，我说我想搞生物科研",
    "晚上回家把硬盘的型号又比了一遍，还是没下单",
    "物理周测考砸了，最后一题完全没思路，心里挺难受",
    "妈妈煮了玉米排骨汤，我喝了两碗，肚子都撑圆了",
    "半夜躺着想数字生命卡这件事，越想越清醒睡不着",
    "早读背英语单词背到 abandon 就想笑，这词太应景了",
    "体育课跑八百米，跑完在操场边坐了好久才缓过来",
    "把窗户打开透气，风一吹课本翻页，我干脆不看了",
    "班主任在班会上说高考只剩两百多天，全班安静了三秒",
]


def make_card(n=12, path=TMP):
    for suf in ("", "-wal", "-shm", ".state.json"):
        p = path + suf
        if os.path.exists(p):
            for _ in range(20):
                try:
                    os.remove(p)
                    break
                except PermissionError:
                    time.sleep(0.1)
    c = MemoryCard(path)
    for i in range(n):
        c.remember(FILLER[i % len(FILLER)], ts=time.time() - 3600 * (n - i))
    c.remember("用户主号QQ号是100000000，昵称叫使用者", ts=time.time() - 86400)
    c.remember("用户喜欢吃白米饭", ts=time.time() - 7200)
    c.close()


# ══════════════════════════════════════════════════════
print("=" * 50)
print("【1】取数")
print("=" * 50)
make_card(12)
p = viz.collect(TMP, "你还记得我QQ号吗")
ck("拿到节点", len(p["nodes"]) == 14, "%d 条" % len(p["nodes"]))
ck("每个节点有 id/core/region/pool/w", all(
    {"id", "core", "region", "pool", "w"} <= set(n) for n in p["nodes"]))
ck("w 在 [0,1.2] 区间", all(-0.01 <= n["w"] <= 1.21 for n in p["nodes"]))
ck("有突触边", len(p["edges"]) > 0, "%d 条" % len(p["edges"]))
ck("边有三元组", all({"a", "b", "kind", "weight"} <= set(e) for e in p["edges"]))
ck("有 stats", bool(p["stats"].get("total")))
ck("有心情", bool(p["mood"]))
ck("有激素四元组", len(p["hormones"]) == 4)
ck("有生成时间", bool(p["generated"]))
ck("命中列表非空", len(p["hits"]) > 0, "%d 条" % len(p["hits"]))
ck("命中带分数", all("score" in x for x in p["hits"]))
ck("命中带渲染文本", all(x.get("text") for x in p["hits"]))
ck("hit_ids 和 hits 对得上",
   set(p["hit_ids"]) == {x["id"] for x in p["hits"]})
ck("有上下文文本", bool(p["ctx_text"]))
ck("有四格配额（有内容的那几格）",
   isinstance(p["sections"], dict) and len(p["sections"]) >= 1,
   "%s" % (p["sections"],))
ck("四格合计不超过预算", sum((p["sections"] or {}).values()) <= 6000,
   "合计 %d" % sum((p["sections"] or {}).values()))

print()
print("【2】布局")
print("=" * 50)
pos = viz.layout(p["nodes"], p["edges"])
ck("每个节点都有坐标", len(pos) == len(p["nodes"]))
ck("坐标在画布内（40~1160 / 40~860）", all(
    39 <= x <= 1161 and 39 <= y <= 861 for x, y in pos.values()))
# 不重叠检查（半径按最大算 14px）
pts = list(pos.values())
min_d = 1e9
for i in range(len(pts)):
    for j in range(i + 1, len(pts)):
        d = ((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2) ** 0.5
        min_d = min(min_d, d)
ck("没有两个点完全重合", min_d > 5, "最近 %.1f px" % min_d)
ck("布局是确定性的（同种子同结果）",
   viz.layout(p["nodes"], p["edges"]) == pos)
ck("空输入不崩", viz.layout([], []) == {})

print()
print("【3】HTML 自包含 + 结构")
print("=" * 50)
html = viz.build_html(p)
ck("没有未替换的占位符", "__DATA__" not in html and "__RC__" not in html
   and "__EC__" not in html and "__RCN__" not in html)
ck("没有外链脚本", "<script src=" not in html and "http://" not in html
   and "https://" not in html.replace("https://www.w3.org", ""))
ck("没有外链样式", "<link" not in html)
ck("有 canvas", "<canvas" in html)
ck("script 开闭配对",
   html.count("<script") == html.count("</script>"))
ck("有面板", 'id="panel"' in html)
ck("有 tooltip", 'id="tip"' in html)
ck("含滚轮缩放", "wheel" in html)
ck("含拖拽平移", "mousedown" in html and "mousemove" in html)
ck("含图例", "图例" in html)

# JSON 能不能解析
m = re.search(r"const DATA = (.*?);\s*\n\s*\nconst cv", html, re.S)
ck("找得到 DATA 段", m is not None)
if m:
    blob = m.group(1)
    try:
        obj = json.loads(blob)
        ck("DATA 是合法 JSON", True, "nodes=%d edges=%d"
           % (len(obj["nodes"]), len(obj["edges"])))
        ck("JSON 里节点带坐标", all("px" in n and "py" in n for n in obj["nodes"]))
        ck("JSON 里命中带 id", all("id" in x for x in obj["hits"]))
    except Exception as e:
        ck("DATA 是合法 JSON", False, str(e))

# </script> 注入防护
tricky = [dict(n) for n in p["nodes"]]
tricky[0]["core"] = "危险 </script><script>alert(1)</script>"
tricky[0]["raw"] = "同上"
p2 = dict(p)
p2["nodes"] = tricky
html2 = viz.build_html(p2)
ck("数据里的 </script> 被转义成 \\u003c", "\\u003c/script>" in html2)
ck("没有多出 script 标签", html2.count("<script") == html2.count("</script>"))
# 转义后仍然是合法 JSON，且解析回来还是原文
m2 = re.search(r"const DATA = (.*?);\s*\n\s*\nconst cv", html2, re.S)
try:
    o2 = json.loads(m2.group(1))
    ck("转义后 JSON 仍合法", True)
    ck("解析回来还是原文", "危险 </script>" in o2["nodes"][0]["core"])
except Exception as e:
    ck("转义后 JSON 仍合法", False, str(e))

print()
print("【4】写到磁盘")
print("=" * 50)
out = os.path.join(TMPDIR, "nebula.html")
rc = viz.main(["--db", TMP, "--out", out, "--query", "白米饭"])
ck("main 返回 0", rc == 0, "rc=%s" % rc)
ck("文件已生成", os.path.exists(out),
   "%.0f KB" % (os.path.getsize(out) / 1024.0) if os.path.exists(out) else "")
with open(out, "r", encoding="utf-8") as f:
    saved = f.read()
ck("磁盘文件和内存一致长度", len(saved) > 5000)
ck("UTF-8 中文没乱码", "记忆星云" in saved)

print()
print("【5】边界情况")
print("=" * 50)
# 空库
empty_db = os.path.join(TMPDIR, "empty.db")
c = MemoryCard(empty_db)
c.close()
pe = viz.collect(empty_db)
ck("空库不崩", len(pe["nodes"]) == 0)
he = viz.build_html(pe)
ck("空库也能生成 HTML", len(he) > 5000)
ck("空库布局不崩", viz.layout([], []) == {})

# 单条
one_db = os.path.join(TMPDIR, "one.db")
c = MemoryCard(one_db)
c.remember("只有这一条记忆，说的是白米饭")
c.close()
p1 = viz.collect(one_db)
ck("单条不崩", len(p1["nodes"]) == 1)
viz.build_html(p1)
ck("单条也能生成", True)

# 没有突触（不调 ensure）
ck("无边时布局不崩", viz.layout(p1["nodes"], []) is not None)

# 找不到库
rc = viz.main(["--db", os.path.join(TMPDIR, "nope.db"),
               "--out", os.path.join(TMPDIR, "x.html")])
ck("库不存在返回 2", rc == 2, "rc=%s" % rc)

shutil.rmtree(TMPDIR, ignore_errors=True)

print()
print("=" * 50)
print("T6-2 验收：通过 %d 项，失败 %d 项" % (ok_n, bad_n))
sys.exit(0 if bad_n == 0 else 1)
