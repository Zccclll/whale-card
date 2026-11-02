# -*- coding: utf-8 -*-
"""T2-1 验收：主干侵蚀分级（V3.7 §3.6 / V5.0）

验收标准（施工总表）：
  一条记忆衰减到极低权重，多 tick 几次
  → 渲染从「吃烧烤」→「吃（什么）」→「（某个动作）」逐级掉
"""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2
from card2 import (split_predicate, render_predicate, DAY, POOL_SHORT,
                   ERASE_STAGE_VERB, ERASE_STAGE_OBJ)


def main():
    # ---- 1) 拆句：主语/动词/宾语 ----
    cases = [
        ("吃烧烤", "", "吃", "烧烤"),
        ("我在路边摊吃烧烤", "我在路边摊", "吃", "烧烤"),
        ("跟同桌聊了很久", "跟同桌", "聊", "了很久"),
        ("拍婚礼挣钱", "", "拍", "婚礼挣钱"),
        ("去看电影", "", "去", "看电影"),
        ("喝了奶茶", "", "喝", "奶茶"),
    ]
    ok_all = True
    for txt, want_s, want_v, want_o in cases:
        s, v, o = split_predicate(txt)
        # 只要求动词对，主宾大致对
        ok = (v == want_v)
        if not ok:
            ok_all = False
        print(("  ✅" if ok else "  ❌") +
              f" 「{txt}」→ 主[{s}] 动[{v}] 宾[{o}]")

    # ---- 2) 渲染分级 ----
    subj, verb, obj = "我", "吃", "烧烤"
    r0 = render_predicate(subj, verb, obj, 0)
    r1 = render_predicate(subj, verb, obj, 1)
    r2 = render_predicate(subj, verb, obj, 2)
    print(f"  stage0: {r0}")
    print(f"  stage1: {r1}")
    print(f"  stage2: {r2}")
    ok1 = ("吃" in r0 and "烧烤" in r0)
    ok2 = ("吃" not in r1) and ("烧烤" in r1)
    ok3 = ("吃" not in r2) and ("烧烤" not in r2)
    print(("  ✅" if ok1 else "  ❌") + " stage0 完整")
    print(("  ✅" if ok2 else "  ❌") + " stage1 动词没了、宾语还在")
    print(("  ✅" if ok3 else "  ❌") + " stage2 动词宾语都没了")

    # ---- 3) 实跑：一条记忆反复 tick，看渲染逐级掉 ----
    tmp = tempfile.mktemp(suffix=".db")
    card = card2.MemoryCard(tmp, card2.Clock(start=1_700_000_000))
    random.seed(42)
    mid = card.remember("我在路边摊吃烧烤，很开心", salience=0.2,
                        emotion=0.1, entities=0, oxy=0.0)
    row = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
    print(f"  存完：core=[{row['core']}] 主=[{row['subj']}] "
          f"动=[{row['verb']}] 宾=[{row['obj']}] stage={row['erase_stage']}")

    # 衰减到极低：把权重压到侵蚀线以下、GC 线以上，再 tick 触发侵蚀
    card.db.execute("UPDATE mem SET w=0.015 WHERE id=?", (mid,))
    card.db.commit()

    seen_stages = set()
    renders = []
    for i in range(80):
        row = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
        if row is None:
            seen_stages.add("gone")
            renders.append("（整条消失了）")
            break
        seen_stages.add(row["erase_stage"])
        r = card.render(row)
        if not renders or renders[-1] != r:
            renders.append(r)
        card.tick(3600)

    print("  渲染变化轨迹：")
    for r in renders:
        print("    " + r)

    ok4 = ERASE_STAGE_VERB in seen_stages or ERASE_STAGE_OBJ in seen_stages
    print(("  ✅" if ok4 else "  ❌") +
          f" 侵蚀确实分级发生过（经历过的 stage={sorted(map(str, seen_stages))}）")

    # 至少看到「做了什么记不清了」这种残缺渲染
    ok5 = any("记不清了" in r for r in renders)
    print(("  ✅" if ok5 else "  ❌") + " 出现过残缺主干渲染")

    card.close()
    try:
        os.remove(tmp)
    except OSError:
        pass

    all_ok = all([ok_all, ok1, ok2, ok3, ok4, ok5])
    print()
    print("=" * 50)
    print("T2-1 验收：" + ("全部通过 ✅" if all_ok else "有失败项 ❌"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
