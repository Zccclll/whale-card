# -*- coding: utf-8 -*-
"""T5-1 五空间下沉 + 动态平衡 验收

验收标准（施工总表）：
  造 1000 条老记忆 → 下沉后热库只剩索引、ARCHIVE 有全文、召回仍能命中；
  模拟 35 年 tick → 该条最终变成 TRACE 计数、总量不再增长。
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import card2
from card2 import (MemoryCard, Clock, DAY, POOL_SHORT, POOL_MEDIUM,
                   POOL_LONG, POOL_ARCHIVE, POOL_DEEP,
                   W_EXIT_LONG, W_EXIT_ARCHIVE, W_EXIT_DEEP,
                   K_ARCHIVE, K_DEEP, LAM_L)

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ✅ %s %s" % (name, extra))
    else:
        fail += 1
        print("  ❌ %s %s" % (name, extra))


def say(s):
    print(s)
    sys.stdout.flush()


def new_card():
    p = tempfile.mktemp(suffix=".db")
    return MemoryCard(p)


_TOPICS = ["今天数学考试有点紧张", "食堂的白米饭很好吃", "物理课讲了电磁感应",
           "同桌说他要去外地读大学", "晚自习看了会儿星星", "妈妈打电话问冷不冷",
           "操场上跑了三圈腿酸", "英语单词背了五十个", "化学实验炸了烧杯",
           "生物老师讲了细胞分裂", "下雨天在走廊看雨", "周末去镇上买了本子",
           "和同学讨论了人工智能", "早上起晚了没吃早饭", "书包里翻出旧照片"]


# ============================================================
say("=== 1. 常量与半衰期自洽 ===")
hl_archive = math.log(2) / K_ARCHIVE / DAY
hl_deep = math.log(2) / K_DEEP / DAY
hl_long = math.log(2) / LAM_L / DAY
check("ARCHIVE 半衰期约 535 天", 520 < hl_archive < 550, "(%.1f 天)" % hl_archive)
check("DEEP 半衰期约 2674 天", 2600 < hl_deep < 2750, "(%.1f 天)" % hl_deep)
check("三池由快到慢 LONG < ARCHIVE < DEEP",
      hl_long < hl_archive < hl_deep,
      "(%.0f / %.0f / %.0f)" % (hl_long, hl_archive, hl_deep))

# ============================================================
say("\n=== 2. weight_now 认识新池子 ===")
c = new_card()
now = c.clock.now()
w_arc = card2.weight_now(1.0, POOL_ARCHIVE, now, now + 535 * DAY)
check("ARCHIVE 535 天后剩一半", abs(w_arc - 0.5) < 0.02, "(%.3f)" % w_arc)
w_deep = card2.weight_now(1.0, POOL_DEEP, now, now + 2674 * DAY)
check("DEEP 2674 天后剩一半", abs(w_deep - 0.5) < 0.02, "(%.3f)" % w_deep)
c.close()

# ============================================================
say("\n=== 3. 造 1000 条老记忆，看下沉 ===")
c = new_card()
c.clock.advance(0)
t0 = c.clock.now()
ids = []
# 直接写库，绕开 remember 的 Jaccard 去重 —— 这一步要测的是「下沉」，
# 不是「编码」。1000 条模板相近的句子会被去重压成几十条，测不出东西。
_rand = __import__("random").Random(20261030)
for i in range(1000):
    txt = "样本%d号：%s" % (i, "".join(
        _rand.choice("天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏")
        for _ in range(12)))
    vec = __import__("json").dumps(card2.to_vec(txt), ensure_ascii=False)
    cur = c.db.execute(
        "INSERT INTO mem(raw, core, vec, pool, w, t_anchor, salience,"
        " create_ts, last_active_ts, region)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (txt, txt, vec, POOL_LONG, 0.03, t0 - 500 * DAY, 0.6,
         t0 - 500 * DAY, t0 - 500 * DAY, "episode"))
    ids.append(cur.lastrowid)
c.db.commit()
say("  造了 1000 条，全部设为「长期池 + 权重 0.03 + 500 天前」")
say("  下沉前：%s" % {k: v["n"] for k, v in c.stats()["pools"].items()})

ev = c.tick(0)
n_arc = c.stats()["pools"][POOL_ARCHIVE]["n"]
check("tick 后有条目下沉到 ARCHIVE", n_arc > 0, "(%d 条)" % n_arc)
check("tick 返回了 sink 事件",
      any("sink->archive" in str(e) for e in ev))

# 继续 tick 直到全部沉下去
for _ in range(10):
    c.tick(0)
st = c.stats()
say("  多轮 tick 后：%s" % {k: v["n"] for k, v in st["pools"].items()})
check("长期池被清空（该沉的都沉了）", st["pools"][POOL_LONG]["n"] == 0,
      "(剩 %d)" % st["pools"][POOL_LONG]["n"])
check("ARCHIVE 拿到了这些记忆", st["pools"][POOL_ARCHIVE]["n"] == 1000,
      "(%d 条)" % st["pools"][POOL_ARCHIVE]["n"])

# ============================================================
say("\n=== 4. ARCHIVE 不参与实时检索 ===")
hit_normal = c.recall("第7条记忆", top_k=3, access=False)
check("默认召回翻不到潜意识里的东西", len(hit_normal) == 0,
      "(翻到 %d 条)" % len(hit_normal))
hit_forced = c.recall("第7条记忆", top_k=3, access=False, pools=[POOL_ARCHIVE])
check("显式点名 ARCHIVE 才翻得到", len(hit_forced) > 0,
      "(翻到 %d 条)" % len(hit_forced))

# ============================================================
say("\n=== 5. 召回能把 ARCHIVE 里的东西挖回长期池（记忆被重新激活）===")
# 先造一条清晰记忆，确保 recall 不会因为"库是空的"而报错
c.close()

c = new_card()
t0 = c.clock.now()
mid = c.remember("我养了一只橘猫叫团子", salience=0.9, ts=t0 - 600 * DAY)
c.db.execute("UPDATE mem SET pool=?, w=0.03, t_anchor=? WHERE id=?",
             (POOL_LONG, t0 - 600 * DAY, mid))
c.db.commit()
c.tick(0)          # 下沉到 ARCHIVE
row = c.db.execute("SELECT pool FROM mem WHERE id=?", (mid,)).fetchone()
check("这条已经沉进 ARCHIVE", row["pool"] == POOL_ARCHIVE, "(%s)" % row["pool"])
before = c.db.execute("SELECT w FROM mem WHERE id=?", (mid,)).fetchone()["w"]
c.recall("橘猫叫什么", top_k=3, access=True, pools=[POOL_ARCHIVE])
c.tick(0)          # 结算召回事件
after_row = c.db.execute("SELECT w, access_count, pool FROM mem WHERE id=?",
                         (mid,)).fetchone()
check("被想起后权重回升", after_row["w"] > before,
      "(%.4f → %.4f)" % (before, after_row["w"]))
check("access_count 有增加", after_row["access_count"] > 0,
      "(%d)" % after_row["access_count"])

# ============================================================
say("\n=== 6. 模拟 35 年：一条记忆走完全程 → TRACE ===")
c.close()
c = new_card()
t0 = c.clock.now()
mid = c.remember("这是三十五年前的一件小事", salience=0.5, ts=t0 - 400 * DAY)
c.db.execute("UPDATE mem SET pool=?, w=0.03, t_anchor=? WHERE id=?",
             (POOL_LONG, t0 - 400 * DAY, mid))
c.db.commit()
say("  起点：长期池，w=0.03")

trail = []
seen = [POOL_LONG]          # 起点就是长期池
for year in range(1, 36):
    c.clock.advance(365 * DAY)
    c.tick(365 * DAY)
    row = c.db.execute("SELECT pool, w FROM mem WHERE id=?", (mid,)).fetchone()
    if row:
        if not seen or seen[-1] != row["pool"]:
            seen.append(row["pool"])
            trail.append((year, row["pool"], round(row["w"], 6)))
    else:
        if not seen or seen[-1] != "TRACE":
            seen.append("TRACE")
            trail.append((year, "TRACE", 0))
        break

say("  走过的池子：")
for yr, pool, w in trail:
    say("    第 %2d 年 → %-8s w=%s" % (yr, pool, w))

tr = c.stats()["trace"]
check("最终变成了 TRACE 计数", tr["memories"] >= 1,
      "(trace 行 %d / 记 %d 条)" % (tr["rows"], tr["memories"]))
check("全程按 LONG→ARCHIVE→DEEP→TRACE 顺序走",
      seen == [POOL_LONG, POOL_ARCHIVE, POOL_DEEP, "TRACE"],
      "(实际 %s)" % "→".join(str(x) for x in seen))

# ============================================================
say("\n=== 7. 总量有界（动态平衡，不会无限涨）===")
c.close()
c = new_card()
t0 = c.clock.now()
# 每天 5 条，跑 3 年，看总量
for day in range(365 * 3):
    ts = t0 + day * DAY
    for j in range(5):
        c.remember("第%d天第%d条" % (day, j), salience=0.5, ts=ts)
    if day % 30 == 0:
        c.clock.advance(30 * DAY)
        c.tick(30 * DAY)
n_mid = c.stats()["total"]
say("  3 年（约 5475 条）后库内条数：%d" % n_mid)
check("库内条数少于总写入数（说明在淘汰/下沉）", n_mid < 5475,
      "(%d / 5475)" % n_mid)

# ============================================================
say("\n=== 8. 兼容：老库（只有 short/medium/long）不受影响 ===")
c.close()
c = new_card()
c.remember("正常记一条", salience=0.7)
st = c.stats()
check("新库 stats 带 trace 字段", "trace" in st)
check("新库五池都在 stats 里",
      all(p in st["pools"] for p in (POOL_SHORT, POOL_MEDIUM, POOL_LONG,
                                     POOL_ARCHIVE, POOL_DEEP)))
check("刚记的一条还在短期池", st["pools"][POOL_SHORT]["n"] == 1)
c.close()

# ============================================================
say("\n" + "=" * 50)
if fail == 0:
    print("全部通过 ✅  (%d 项)" % ok)
else:
    print("失败 %d 项 / 通过 %d 项 ❌" % (fail, ok))
sys.exit(1 if fail else 0)
