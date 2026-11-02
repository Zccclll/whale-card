# -*- coding: utf-8 -*-
"""时态验收（大肥鱼 fork 2026-11-02，借鉴 Zep/Graphiti）

新事实推翻旧事实时，旧事实标失效而不是继续跟新事实抢召回：
  - invalidate：只打标记（valid_until + superseded_by），行不删、正文不动
  - replace_fact：写新条 + 标旧条，一步到位
  - recall 默认过滤失效条目；history=True 可翻旧账
  - history_of：沿 superseded_by 链看世代

验收标准：
  1. replace_fact 后旧条还在，正文一字未动（拔刺原则：不删）
  2. 旧条被标 valid_until=now、superseded_by=新条 id
  3. 默认 recall 不再返回旧条，新条正常
  4. recall(history=True) 能翻出旧条
  5. history_of 链：旧→新 完整
  6. invalidate 幂等（重复调用返回 False）
  7. valid_until 未到期的处境条目不受过滤影响
  8. 出生证/哈希链审计在失效标记后仍然 ok（UPDATE 不碰 raw）
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import card2
from card2 import MemoryCard, Clock, POOL_MEDIUM

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
    card = MemoryCard(fresh(), Clock(start=1_700_000_000))

    # ---- 1~2：replace_fact 标失效不删行 ----
    old = card.remember("老大住在望京写稿子", pool=POOL_MEDIUM, salience=0.8)
    new = card.replace_fact(old, "老大搬去顺义了，工作室也搬",
                           pool=POOL_MEDIUM, salience=0.8)
    ok("新条独立成行（没被去重合并）", new != old, "(old=%s new=%s)" % (old, new))
    r = card.db.execute("SELECT * FROM mem WHERE id=?", (old,)).fetchone()
    ok("旧条还在（不删）", r is not None)
    ok("旧条正文一字未动", "望京" in r["core"], "[%s]" % r["core"])
    ok("旧条被标失效", r["valid_until"] > 0, "(valid_until=%.0f)" % r["valid_until"])
    ok("旧条记上取代者", r["superseded_by"] == new,
       "(superseded_by=%s)" % r["superseded_by"])

    # ---- 3~4：默认检索沉底，history 可翻 ----
    ids_default = [row["id"] for _, _, row in card.recall("望京", top_k=5)]
    ok("默认 recall 不再返回旧条", old not in ids_default,
       "(ids=%s)" % ids_default)
    ok("新条可被正常召回",
       new in [row["id"] for _, _, row in card.recall("顺义", top_k=5)])
    ids_hist = [row["id"] for _, _, row in card.recall("望京", top_k=5, history=True)]
    ok("history=True 翻出旧条", old in ids_hist, "(ids=%s)" % ids_hist)

    # ---- 5：世代链 ----
    chain = card.history_of(old)
    ok("history_of 链完整（旧→新）",
       [g["id"] for g in chain] == [old, new], "(%s)" % [g["id"] for g in chain])

    # ---- 6：幂等 ----
    ok("invalidate 重复调用返回 False", card.invalidate(old) is False)

    # ---- 7：有效期内的处境不受影响 ----
    future = card.clock.now() + 86400 * 300
    sit = card.remember("老大在读高三冲刺班", pool=POOL_MEDIUM, salience=0.8)
    card.set_tier(sit, "core_mutable", kind="situation",
                  valid_until=future)
    ids = [row["id"] for _, _, row in card.recall("高三", top_k=5)]
    ok("未到期处境照常召回", sit in ids, "(ids=%s)" % ids)
    # 到期后走作者原路（situation_expire 降成曾经、valid_until 归零），不被误滤
    card.clock.advance(86400 * 301)
    n = card.situation_expire()
    ids2 = [row["id"] for _, _, row in card.recall("高三", top_k=5)]
    ok("到期处境经 situation_expire 归零后仍可见（不误杀）",
       n >= 1 and sit in ids2, "(expire=%d ids=%s)" % (n, ids2))

    # ---- 8：审计链完好 ----
    import timeline as _tl
    tl = _tl.Timeline(card.db)
    tl.birth_stamp(old)
    tl.birth_stamp(new)
    v_ok, v_bad = tl.verify_birth()
    ok("失效标记后出生证审计仍 ok（UPDATE 不碰 raw）", v_ok,
       "(bad=%s)" % (v_bad[:2],))

    card.close()
    return finish()


def finish():
    print()
    print("=" * 50)
    if FAIL:
        print("时态验收：失败 %d 项 ❌（通过 %d 项）" % (len(FAIL), len(PASS)))
        for n in FAIL:
            print("   - " + n)
        return 1
    print("时态验收：全部通过 ✅  (%d 项)" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
