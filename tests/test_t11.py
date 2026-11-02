# -*- coding: utf-8 -*-
"""T1-1 验收：遗忘与联想解耦（V5.0 公理 6）

验收标准（施工总表）：
  调 recall() 10 次，mem 表权重不变；
  跑一次 tick()，权重按 10 次累积更新。
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2


def snap(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = {r["id"]: dict(r) for r in con.execute("SELECT * FROM mem")}
    det = {}
    for r in con.execute("SELECT id, weight FROM detail"):
        det[r["id"]] = r["weight"]
    con.close()
    return rows, det


def main():
    tmp = tempfile.mktemp(suffix=".db")
    card = card2.MemoryCard(tmp)
    card.remember("10月24日在学校门口的路边摊吃烧烤，很开心", salience=0.8)
    card.remember("我喜欢吃白米饭", salience=0.9)
    card.remember("主号QQ100000000", salience=0.9)

    before, det_before = snap(tmp)

    # ---- 第一步：连调 10 次 recall，权重必须纹丝不动 ----
    for i in range(10):
        card.recall("烧烤", top_k=2)
    after, det_after = snap(tmp)

    ok1 = True
    for mid, b in before.items():
        a = after[mid]
        if abs(a["w"] - b["w"]) > 1e-12:
            ok1 = False
            print(f"  ❌ id={mid} w 变了: {b['w']:.6f} -> {a['w']:.6f}")
        if a["access_count"] != b["access_count"]:
            ok1 = False
            print(f"  ❌ id={mid} access_count 变了: "
                  f"{b['access_count']} -> {a['access_count']}")
        if abs(a["t_anchor"] - b["t_anchor"]) > 1e-9:
            ok1 = False
            print(f"  ❌ id={mid} t_anchor 变了")
    for did, b in det_before.items():
        if abs(det_after.get(did, 0.0) - b) > 1e-12:
            ok1 = False
            print(f"  ❌ detail id={did} weight 变了: {b:.6f} -> {det_after.get(did)}")
    print(("  ✅" if ok1 else "  ❌") + " recall 10 次，mem/detail 一个字节没动")

    # 事件有没有真的记下来
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row
    n_ev = con.execute("SELECT COUNT(*) c FROM recall_event WHERE settled=0").fetchone()["c"]
    con.close()
    ok_ev = n_ev > 0
    print(("  ✅" if ok_ev else "  ❌") + f" recall_event 记了 {n_ev} 条未结算事件")

    # ---- 第二步：跑一次 tick，权重才更新 ----
    card.tick(60)
    settled, _ = snap(tmp)
    changed = [mid for mid, b in before.items()
               if settled[mid]["access_count"] > b["access_count"]]
    ok2 = len(changed) > 0
    print(("  ✅" if ok2 else "  ❌") +
          f" tick 后 {len(changed)} 条记忆的 access_count 增加")

    # 权重确实抬了（且没超过 1.0）
    ok3 = True
    for mid in changed:
        if settled[mid]["w"] < before[mid]["w"] - 1e-12:
            ok3 = False
            print(f"  ❌ id={mid} tick 后权重反而降了")
        if settled[mid]["w"] > 1.0 + 1e-9:
            ok3 = False
            print(f"  ❌ id={mid} 权重超过 1.0")
    print(("  ✅" if ok3 else "  ❌") + " tick 后权重只升不降且 <= 1.0")

    # ---- 第三步：幂等性 —— 再 tick 一次，不该重复加 ----
    acc_after_first = {mid: settled[mid]["access_count"] for mid in settled}
    card.tick(60)
    settled2, _ = snap(tmp)
    ok4 = all(settled2[mid]["access_count"] == acc_after_first[mid]
              for mid in settled2)
    print(("  ✅" if ok4 else "  ❌") + " 再 tick 一次不重复结算（幂等）")

    # ---- 第四步：settled 标记清零检查 ----
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row
    n_unsettled = con.execute(
        "SELECT COUNT(*) c FROM recall_event WHERE settled=0").fetchone()["c"]
    n_total = con.execute("SELECT COUNT(*) c FROM recall_event").fetchone()["c"]
    con.close()
    ok5 = n_unsettled == 0 and n_total > 0
    print(("  ✅" if ok5 else "  ❌") +
          f" 事件全部结算：total={n_total}, unsettled={n_unsettled}")

    # ---- 第五步：旧库兼容 —— 老 card.db 能正常打开 ----
    ok6 = True
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.db")
    if not os.path.exists(legacy):
        # 修复（大肥鱼 2026-11-02）：原来文件不存在也直接 MemoryCard(legacy)，
        # 会顺手造出一个空 card.db —— 自己判失败，还污染 test_card2.py
        # 第 9 节「文件不存在则跳过」的守卫。缺夹具就干净跳过。
        print("  （跳过老库兼容：tests/card.db 夹具不随仓库发布）")
    else:
        try:
            old = card2.MemoryCard(legacy)
            st = old.stats()
            ok6 = st["total"] >= 323
            print(("  ✅" if ok6 else "  ❌") +
                  f" 老库兼容：{st['total']} 条")
            old.close()
        except Exception as e:
            ok6 = False
            print(f"  ❌ 老库打开失败: {e}")

    card.close()
    try:
        os.remove(tmp)
    except OSError:
        pass

    all_ok = all([ok1, ok_ev, ok2, ok3, ok4, ok5, ok6])
    print()
    print("=" * 50)
    print("T1-1 验收：" + ("全部通过 ✅" if all_ok else "有失败项 ❌"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
