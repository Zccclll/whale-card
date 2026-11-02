# -*- coding: utf-8 -*-
"""T1-3 验收：Oxy_bond 更新（V3.7 §6 / V5.0 补回）

验收标准（施工总表）：
  说 20 句亲密话 → oxy 上升；
  说 3 句伤人的话 → oxy 下降但不归零。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2
from card2 import update_oxy, oxy_signal, ETA_OXY_UP, ETA_OXY_DN


def main():
    # ---- 1) 纯函数：正向饱和 ----
    o = 0.0
    for _ in range(20):
        o = update_oxy(o, 1.0, 0.0)
    ok1 = 0.5 < o < 1.0
    print(("  ✅" if ok1 else "  ❌") + f" 20 次满格正向 → oxy={o:.4f}（应 >0.5 且 <1）")

    # 饱和性：继续加，增量越来越小
    o2 = update_oxy(o, 1.0, 0.0)
    delta_far = o2 - o
    o3 = update_oxy(0.0, 1.0, 0.0)
    delta_near = o3 - 0.0
    ok2 = delta_far < delta_near
    print(("  ✅" if ok2 else "  ❌") +
          f" 饱和性：初始增量 {delta_near:.4f} > 后期增量 {delta_far:.4f}")

    # ---- 2) 纯函数：负向带惯性，不归零 ----
    o = 0.9
    for _ in range(3):
        o = update_oxy(o, 0.0, 1.0)
    ok3 = 0.0 < o < 0.9
    print(("  ✅" if ok3 else "  ❌") + f" 3 次满格负向 → oxy={o:.4f}（应降但不归零）")

    # 一次翻脸测试：极高 oxy 遇一次满格负向，不能直接掉到 0
    o = 1.0
    o = update_oxy(o, 0.0, 1.0)
    ok4 = o > 0.5
    print(("  ✅" if ok4 else "  ❌") + f" 一次冲突不会翻脸：1.0 → {o:.4f}（应 >0.5）")

    # ---- 3) 信号提取 ----
    sp, sm = oxy_signal("谢谢你一直陪着我，我好喜欢你")
    ok5 = sp > 0 and sm == 0
    print(("  ✅" if ok5 else "  ❌") + f" 亲密句信号 s+={sp:.2f} s-={sm:.2f}")

    sp, sm = oxy_signal("我讨厌你，烦死你了，滚")
    ok6 = sm > 0 and sp == 0
    print(("  ✅" if ok6 else "  ❌") + f" 伤人句信号 s+={sp:.2f} s-={sm:.2f}")

    sp, sm = oxy_signal("今天数学考了85分")
    ok7 = sp == 0 and sm == 0
    print(("  ✅" if ok7 else "  ❌") + f" 中性句不扰动 s+={sp:.2f} s-={sm:.2f}")

    # ---- 4) 落到 MemoryCard 上 ----
    tmp = tempfile.mktemp(suffix=".db")
    card = card2.MemoryCard(tmp, card2.Clock(start=1_700_000_000))
    o0 = card.oxy
    for i in range(20):
        card.remember(f"谢谢你陪着我，我真的很喜欢你，这是第{i}次说")
    o1 = card.oxy
    ok8 = o1 > o0 + 0.3
    print(("  ✅" if ok8 else "  ❌") + f" 说 20 句亲密话：oxy {o0:.3f} → {o1:.3f}")

    for i in range(3):
        card.remember(f"我讨厌你这样，烦死了，滚开第{i}次")
    o2 = card.oxy
    ok9 = 0.0 < o2 < o1
    print(("  ✅" if ok9 else "  ❌") + f" 说 3 句伤人的话：oxy {o1:.3f} → {o2:.3f}")

    # ---- 5) 持久化：重开库 oxy 还在 ----
    card.close()
    card_b = card2.MemoryCard(tmp, card2.Clock(start=1_700_000_000))
    ok10 = abs(card_b.oxy - o2) < 1e-9
    print(("  ✅" if ok10 else "  ❌") +
          f" 重开库 oxy 保持：{o2:.4f} → {card_b.oxy:.4f}")

    # ---- 6) 记忆条目自己也带上亲密标记 ----
    row = card_b.db.execute(
        "SELECT oxy FROM mem ORDER BY id ASC LIMIT 1").fetchone()
    ok11 = row["oxy"] > 0.3
    print(("  ✅" if ok11 else "  ❌") +
          f" 亲密记忆条目自带 oxy={row['oxy']:.3f}（进海马打分用）")

    card_b.close()
    try:
        os.remove(tmp)
    except OSError:
        pass

    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8, ok9, ok10, ok11])
    print()
    print("=" * 50)
    print("T1-3 验收：" + ("全部通过 ✅" if all_ok else "有失败项 ❌"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
