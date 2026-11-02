# -*- coding: utf-8 -*-
"""T1-2 验收：短期池容量驱逐 N_SHORT_MAX = 32

验收标准（施工总表）：
  塞 100 条进短期池 → 稳定在 32 条，被踢的高分那条进了 medium
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2
from card2 import DAY, POOL_SHORT, POOL_MEDIUM, POOL_LONG, N_SHORT_MAX


def main():
    tmp = tempfile.mktemp(suffix=".db")
    card = card2.MemoryCard(tmp, card2.Clock(start=1_700_000_000))

    # 塞 100 条进短期池。前 3 条做成高分（高情绪+高显著），其余平庸。
    # 注意：文本必须彼此差异大，否则会被 DEDUPE_JACCARD=0.72 合并掉。
    TOPICS = ["数学考试", "物理作业", "英语听力", "语文作文", "化学实验",
              "生物笔记", "历史背诵", "地理读图", "体育跑步", "音乐欣赏",
              "画画练习", "编程调试", "看书笔记", "洗碗扫地", "买菜做饭",
              "坐公交", "等红灯", "打水喝", "剪指甲", "整理书包"]
    ACTS = ["跟同桌争了两句", "在走廊撞见老师", "听见广播响", "被点名回答问题",
            "偷偷睡了五分钟", "把笔弄丢了", "看见窗外的鸟", "接到妈妈的电话",
            "想起一件旧事", "发现桌上多了张纸条"]
    ids = []
    for i in range(100):
        topic = TOPICS[i % len(TOPICS)]
        act = ACTS[(i // len(TOPICS)) % len(ACTS)]
        txt = f"{topic}的时候{act}，是第{i}回"
        if i < 3:
            mid = card.remember(
                f"{topic}那天我在学校门口特别开心地哭了一场，这是第{i}回，"
                f"很重要", salience=0.95, emotion=1.0, entities=5, oxy=1.0)
        else:
            mid = card.remember(txt, salience=0.2)
        ids.append(mid)

    n0 = card.db.execute(
        "SELECT COUNT(*) FROM mem WHERE pool='short'").fetchone()[0]
    print(f"  塞完：短期池 {n0} 条")

    # 触发驱逐（tick 一小步，不跨 7 天）
    card.tick(60)

    n1 = card.db.execute(
        "SELECT COUNT(*) FROM mem WHERE pool='short'").fetchone()[0]
    n_m = card.db.execute(
        "SELECT COUNT(*) FROM mem WHERE pool='medium'").fetchone()[0]
    n_l = card.db.execute(
        "SELECT COUNT(*) FROM mem WHERE pool='long'").fetchone()[0]
    total = card.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
    print(f"  驱逐后：short={n1} medium={n_m} long={n_l} total={total}")

    ok1 = n1 == N_SHORT_MAX
    print(("  ✅" if ok1 else "  ❌") +
          f" 短期池稳定在 {n1} 条（上限 {N_SHORT_MAX}）")

    ok2 = total <= 100
    print(("  ✅" if ok2 else "  ❌") + f" 没有凭空多出记忆（total={total}）")

    # 高分那 3 条必须还在（要么还在 short，要么进了 medium/long）
    survived = 0
    for mid in ids[:3]:
        r = card.db.execute(
            "SELECT pool FROM mem WHERE id=?", (mid,)).fetchone()
        if r is not None:
            survived += 1
            print(f"     高分记忆 id={mid} 存活，pool={r['pool']}")
    ok3 = survived == 3
    print(("  ✅" if ok3 else "  ❌") +
          f" 3 条高分记忆全部存活（{survived}/3）")

    # 被踢的走海马：造一批「被踢但分数够进中期」的
    # 单独测一次：塞 40 条高分（全都会被踢一部分），看有没有进 medium/long
    tmp2 = tempfile.mktemp(suffix=".db")
    card2_ = card2.MemoryCard(tmp2, card2.Clock(start=1_700_000_000))
    for i in range(40):
        card2_.remember(
            f"高情绪事件第{i}号：那天我在操场上哭得很厉害，非常重要",
            salience=0.95, emotion=1.0, entities=5, oxy=1.0)
    card2_.tick(60)
    m2 = card2_.db.execute(
        "SELECT COUNT(*) FROM mem WHERE pool='medium'").fetchone()[0]
    l2 = card2_.db.execute(
        "SELECT COUNT(*) FROM mem WHERE pool='long'").fetchone()[0]
    s2 = card2_.db.execute(
        "SELECT COUNT(*) FROM mem WHERE pool='short'").fetchone()[0]
    print(f"  全高分场景：short={s2} medium={m2} long={l2}")
    ok4b = (m2 + l2) >= 1
    print(("  ✅" if ok4b else "  ❌") +
          f" 被踢的高分记忆走了海马判定并留下（medium={m2}, long={l2}）")
    card2_.close()
    try:
        os.remove(tmp2)
    except OSError:
        pass
    ok4 = ok4b

    card.close()
    try:
        os.remove(tmp)
    except OSError:
        pass

    all_ok = all([ok1, ok2, ok3, ok4])
    print()
    print("=" * 50)
    print("T1-2 验收：" + ("全部通过 ✅" if all_ok else "有失败项 ❌"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
