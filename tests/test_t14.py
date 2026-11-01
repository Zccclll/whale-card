# -*- coding: utf-8 -*-
"""T1-4 验收：SessionCache 双回流（V3.7 §9 / V5.0）

验收标准（施工总表）：
  聊一轮提到某条旧事但没被 recall 命中 → 该条 access_count 仍 +1
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2
from card2 import SESSION_FLUSH_TURNS, DAY


def acc(card, mid):
    return card.db.execute(
        "SELECT access_count FROM mem WHERE id=?", (mid,)).fetchone()["access_count"]


def main():
    tmp = tempfile.mktemp(suffix=".db")
    card = card2.MemoryCard(tmp, card2.Clock(start=1_700_000_000))

    # 一条旧事，存进长期池，之后不再主动 recall
    old = card.remember(
        "去年冬天我养了一只橘猫叫团子，它特别黏人，每天蹲在暖气片上",
        salience=0.9, pool=card2.POOL_LONG)

    a0 = acc(card, old)
    print(f"  旧记忆 id={old} 初始 access_count={a0}")

    # ---- 1) 聊 20 轮，其中第 5 轮顺嘴提到「橘猫团子」，但没人调 recall ----
    for i in range(SESSION_FLUSH_TURNS):
        if i == 5:
            card.session_turn("说起来我家那只橘猫团子现在怎么样了",
                              "它应该还在暖气片上蹲着呢")
        else:
            card.session_turn(f"第{i}轮随便聊点别的，天气不错", "嗯嗯")
    a1 = acc(card, old)
    n_ev = card.db.execute(
        "SELECT COUNT(*) c FROM recall_event WHERE mem_id=? AND settled=0",
        (old,)).fetchone()["c"]
    ok1 = (a1 == a0) and n_ev >= 1
    print(("  ✅" if ok1 else "  ❌") +
          f" 攒满 20 轮自动回流：写了 {n_ev} 条未结算事件，"
          f"access_count 仍 {a0}（公理 6：事件不直接改 mem）")

    # ---- 2) 事件写进去了，且来源标记为 session ----
    row = card.db.execute(
        "SELECT source, rank FROM recall_event WHERE mem_id=?"
        " ORDER BY id DESC LIMIT 1", (old,)).fetchone()
    ok2 = row is not None and row["source"] == "session" and row["rank"] == 99
    print(("  ✅" if ok2 else "  ❌") +
          f" 事件来源标记正确：source={row['source'] if row else None}"
          f" rank={row['rank'] if row else None}")

    # ---- 3) 还没 tick，mem 不动（公理 6 仍然成立）----
    ok3 = acc(card, old) == a1
    print(("  ✅" if ok3 else "  ❌") + f" 回流后未 tick，mem 仍未动")
    # ---- 4) tick 后真正加固 ----
    card.tick(60)
    a2 = acc(card, old)
    ok4 = a2 == a1 + 1
    print(("  ✅" if ok4 else "  ❌") +
          f" tick 后结算：access_count {a1} → {a2}")

    # ---- 5) 已经被 recall 命中的，不重复计 ----
    mid2 = card.remember("我主号QQ是100000000", salience=0.9,
                         pool=card2.POOL_LONG)
    a_before = acc(card, mid2)
    card.recall("QQ号多少", top_k=3)        # 主动召回，记 recall 事件
    # 紧接着在会话里又提一次同一件事
    for i in range(SESSION_FLUSH_TURNS):
        card.session_turn("我主号QQ是100000000没错吧", "对")
    n_events = card.db.execute(
        "SELECT COUNT(*) c FROM recall_event WHERE mem_id=? AND source='session'",
        (mid2,)).fetchone()["c"]
    ok5 = n_events == 0
    print(("  ✅" if ok5 else "  ❌") +
          f" recall 已命中的不重复记 session 事件（session 事件数={n_events}）")

    # ---- 6) 不相关的话不产生事件 ----
    mid3 = card.remember("我在学校三楼的教室上课", salience=0.8,
                         pool=card2.POOL_LONG)
    n0 = card.db.execute(
        "SELECT COUNT(*) c FROM recall_event WHERE mem_id=?",
        (mid3,)).fetchone()["c"]
    for i in range(SESSION_FLUSH_TURNS):
        card.session_turn("今天股票涨了吗我觉得还行吧", "不知道呢")
    n1 = card.db.execute(
        "SELECT COUNT(*) c FROM recall_event WHERE mem_id=?",
        (mid3,)).fetchone()["c"]
    ok6 = n1 == n0
    print(("  ✅" if ok6 else "  ❌") +
          f" 不相关的轮次不产生事件（{n0} → {n1}）")

    # ---- 7) 手动 flush 也работает ----
    card.session_turn("团子那只橘猫真可爱", "")
    n_flush = card.session_flush()
    ok7 = n_flush >= 1
    print(("  ✅" if ok7 else "  ❌") + f" 手动 session_flush() 生效（{n_flush} 条）")

    card.close()
    try:
        os.remove(tmp)
    except OSError:
        pass

    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6, ok7])
    print()
    print("=" * 50)
    print("T1-4 验收：" + ("全部通过 ✅" if all_ok else "有失败项 ❌"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
