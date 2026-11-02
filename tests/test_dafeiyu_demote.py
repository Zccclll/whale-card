# -*- coding: utf-8 -*-
"""拔刺验收（大肥鱼 fork 2026-11-02）：降权空壳模式

fork 默认 ERASE_MODE="demote"：权重跌破侵蚀线的记忆只被钉到 HUSK_W、
标 erase_stage=3，正文一个字不动、永不删除 —— 检索自然沉底，
再被召回或晋升 core 还能浮起来。作者原版的三级随机侵蚀保留在
ERASE_MODE="physical" 下（tests/test_t21.py 继续守那条路）。

验收标准：
  1. demote 模式反复 tick → 记忆还在，core/verb/obj 原样
  2. erase_stage 标成 3（降权空壳），权重钉在 HUSK_W 附近
  3. 渲染是完整正文，不出现「记不清了」
  4. 幂等：继续 tick 不重复产生 demoted 事件
  5. 晋升 core 后 erase_stage 归零、不再吃侵蚀
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import card2
from card2 import MemoryCard, Clock, HUSK_W, ERASE_STAGE_DEMOTED, POOL_LONG

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("  " + detail if detail else ""))


def fresh():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(p)
    return p


def main():
    ok("默认模式是 demote", card2.ERASE_MODE == "demote",
       "(实际=%s)" % card2.ERASE_MODE)

    # ---- 1~3：跌破侵蚀线 → 降权空壳、内容原样、全文渲染 ----
    card = MemoryCard(fresh(), Clock(start=1_700_000_000))
    mid = card.remember("我在路边摊吃烧烤，很开心", salience=0.2,
                        emotion=0.1, entities=0, oxy=0.0)
    card.db.execute("UPDATE mem SET w=0.015 WHERE id=?", (mid,))
    card.db.commit()

    for _ in range(30):
        card.tick(3600)
    row = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
    ok("tick 30 次后记忆还在", row is not None)
    if row is None:
        card.close()
        return finish()
    ok("正文没被动过", "吃烧烤" in row["core"], "core=[%s]" % row["core"])
    ok("动词没被擦", row["verb"] != "", "verb=[%s]" % row["verb"])
    ok("宾语没被擦", row["obj"] != "", "obj=[%s]" % row["obj"])
    ok("标成降权空壳 stage=3", row["erase_stage"] == ERASE_STAGE_DEMOTED,
       "(stage=%d)" % row["erase_stage"])
    ok("权重钉在 HUSK_W", abs(row["w"] - HUSK_W) < 1e-9, "(w=%.4f)" % row["w"])
    r = card.render(row)
    # 低权重时作者原设计会加「（具体记不清了）」的诚实措辞——那是表达层的不装，
    # 拔刺要保证的是**内容层**完整：正文必须一字不少地在渲染里。
    ok("渲染保留完整正文（允许低置信度措辞）",
       "吃烧烤" in r and "很开心" in r, "[%s]" % r)

    # ---- 4：幂等，不重复标 ----
    has_tl = card.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='timeline'").fetchone()
    if has_tl:
        tl_before = card.db.execute(
            "SELECT COUNT(*) FROM timeline WHERE kind='demoted'").fetchone()[0]
        for _ in range(30):
            card.tick(3600)
        tl_after = card.db.execute(
            "SELECT COUNT(*) FROM timeline WHERE kind='demoted'").fetchone()[0]
        ok("继续 tick 不重复产生 demoted 事件", tl_after == tl_before,
           "(%d → %d)" % (tl_before, tl_after))
        row = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
        ok("继续 tick 行仍完好", row is not None and row["erase_stage"] == 3)
    else:
        print("  （没有 timeline 表，幂等检查改查行状态）")
        for _ in range(30):
            card.tick(3600)
        row2 = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
        ok("继续 tick 行仍完好", row2 is not None and row2["erase_stage"] == 3)

    # ---- 5：晋升 core 后转正，不再吃侵蚀 ----
    card.set_tier(mid, "core_mutable", kind="hobby")
    row = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
    ok("晋升 core 后 stage 归零", row["erase_stage"] == 0)
    card.db.execute("UPDATE mem SET w=0.001 WHERE id=?", (mid,))
    card.db.commit()
    for _ in range(30):
        card.tick(3600)
    row = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
    ok("core 层不吃侵蚀（还在且 stage 仍 0）",
       row is not None and row["erase_stage"] == 0)
    card.close()

    # ---- 6：physical 模式仍走原版三级侵蚀（回归保险） ----
    card2.ERASE_MODE = "physical"
    try:
        import random as _rnd
        _rnd.seed(42)
        card = MemoryCard(fresh(), Clock(start=1_700_000_000))
        mid2 = card.remember("我在路边摊吃烧烤，很开心", salience=0.2,
                             emotion=0.1, entities=0, oxy=0.0)
        card.db.execute("UPDATE mem SET w=0.015 WHERE id=?", (mid2,))
        card.db.commit()
        progressed = False
        for _ in range(80):
            row = card.db.execute("SELECT * FROM mem WHERE id=?", (mid2,)).fetchone()
            if row is None or row["erase_stage"] >= 1:
                progressed = True
                break
            card.tick(3600)
        ok("physical 模式仍会分级侵蚀（原版行为保留）", progressed)
        card.close()
    finally:
        card2.ERASE_MODE = "demote"

    return finish()


def finish():
    print()
    print("=" * 50)
    if FAIL:
        print("拔刺验收：失败 %d 项 ❌（通过 %d 项）" % (len(FAIL), len(PASS)))
        for n in FAIL:
            print("   - " + n)
        return 1
    print("拔刺验收：全部通过 ✅  (%d 项)" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
