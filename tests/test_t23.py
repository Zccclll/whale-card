# -*- coding: utf-8 -*-
"""T2-3 验收：心境引擎 k_mood

验收标准（施工总表）：
  连续 3 天负面事件 → M 明显下移；之后 2 天正面 → 缓慢回升
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2
from card2 import (MemoryCard, Clock, DAY, mood_of_event, update_mood,
                   mood_drift_home, mood_text, MOOD_NEUTRAL)


def main():
    ok_all = True

    # ---- 1) 事件效价折算 ----
    cases = [
        ("今天考砸了，很难受", 1.0, "neg"),
        ("我很开心！", 1.0, "pos"),
        ("明天上午有课", 0.25, "neutral"),
        ("谢谢你我爱你", 1.0, "pos"),
        ("你别说了，烦死", 1.0, "neg"),
    ]
    for txt, emo, kind in cases:
        e = mood_of_event(txt, emo)
        if kind == "neg":
            ok = e < MOOD_NEUTRAL - 0.05
        elif kind == "pos":
            ok = e > MOOD_NEUTRAL + 0.05
        else:
            ok = abs(e - MOOD_NEUTRAL) < 1e-6
        print(("  ✅" if ok else "  ❌") + f" 效价 「{txt}」→ {e:.3f} ({kind})")
        ok_all &= ok

    # ---- 2) 慢：一句话推不翻 ----
    m = MOOD_NEUTRAL
    m1 = update_mood(m, 0.05)     # 一个很负面的事件
    delta = MOOD_NEUTRAL - m1
    ok = 0.02 < delta < 0.10
    print(("  ✅" if ok else "  ❌") +
          f" 单次强负面只下移 {delta:.3f}（慢变量，不会一句翻脸）")
    ok_all &= ok

    # ---- 3) 连续 3 天负面 → 明显下移 ----
    tmp = tempfile.mktemp(suffix=".db")
    card = MemoryCard(tmp, Clock(start=1_700_000_000))
    print(f"  初始心境：{card.mood:.3f} ({card.mood_label()})")

    negatives = [
        "今天考砸了，很难受",
        "被老师批评了，心里很堵，难过",
        "又跟同桌吵架了，很烦，委屈",
    ]
    for i, txt in enumerate(negatives):
        for _ in range(6):          # 一天里说了 6 句负面
            card.remember(txt + f"（第{i}天）", salience=0.6)
        card.tick(DAY)
        print(f"  第{i+1}天末：M={card.mood:.3f} ({card.mood_label()})")

    m_neg = card.mood
    ok = m_neg < MOOD_NEUTRAL - 0.05
    print(("  ✅" if ok else "  ❌") +
          f" 3 天负面后 M={m_neg:.3f} 明显下移（< 0.45）")
    ok_all &= ok

    # ---- 4) 之后 2 天正面 → 缓慢回升 ----
    positives = [
        "今天很开心，考试考好了",
        "跟朋友玩得很高兴，舒服",
    ]
    for i, txt in enumerate(positives):
        for _ in range(6):
            card.remember(txt + f"（好转第{i}天）", salience=0.6)
        card.tick(DAY)
        print(f"  好转第{i+1}天末：M={card.mood:.3f} ({card.mood_label()})")

    m_pos = card.mood
    ok = m_pos > m_neg
    print(("  ✅" if ok else "  ❌") +
          f" 2 天正面后 M 回升：{m_neg:.3f} → {m_pos:.3f}")
    ok_all &= ok
    # 「缓慢」：两天不能直接冲到 0.6 以上
    ok2 = m_pos < 0.62
    print(("  ✅" if ok2 else "  ❌") +
          f" 回升是缓慢的（{m_pos:.3f} < 0.62，没有一步回到晴天）")
    ok_all &= ok2

    # ---- 5) 时间回归：放着不管也会往中性漂 ----
    m_low = 0.10
    m_after = mood_drift_home(m_low, 10.0)
    ok = m_after > m_low and m_after < 0.5
    print(("  ✅" if ok else "  ❌") +
          f" 10 天不管：{m_low:.2f} → {m_after:.3f}（往中性漂但没漂满）")
    ok_all &= ok

    # ---- 6) 落盘 + 重开库仍在 ----
    saved = card.mood
    card.close()
    card2b = MemoryCard(tmp, Clock(start=1_700_000_000 + 40 * DAY))
    ok = abs(card2b.mood - saved) < 1e-6
    print(("  ✅" if ok else "  ❌") +
          f" 重开库心境保留：{saved:.3f} → {card2b.mood:.3f}")
    ok_all &= ok

    # ---- 7) 标签翻译 ----
    labels = [mood_text(x) for x in (0.05, 0.30, 0.50, 0.70, 0.95)]
    print(f"  标签：{labels}")
    ok = len(set(labels)) >= 4
    print(("  ✅" if ok else "  ❌") + " 心境标签有区分度")
    ok_all &= ok

    # ---- 8) stats 里能看到 ----
    st = card2b.stats()
    ok = ("mood" in st and "mood_label" in st)
    print(("  ✅" if ok else "  ❌") +
          f" stats 暴露心境：mood={st.get('mood')} label={st.get('mood_label')}")
    ok_all &= ok

    card2b.close()
    try:
        os.remove(tmp)
    except OSError:
        pass

    print()
    print("=" * 50)
    print("T2-3 验收：" + ("全部通过 ✅" if ok_all else "有失败项 ❌"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
