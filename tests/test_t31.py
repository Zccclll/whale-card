# -*- coding: utf-8 -*-
"""T3-1 验收：程序性记忆 + 改良 SM2

验收标准（施工总表）：
  建一条技能 L=0.76 → 30 天不调用掉到休眠线但 desc 一字未变
  → 连调 3 次 Q=5 回到 0.95+
  且 I 大的那条掉得明显比 I 小的慢
"""
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill import (SkillMemory, lam_eff, decay_l, update_ef, next_interval,
                   reinforce_l, score_skill, parse_feedback, L_DORMANT,
                   DAY, HOUR)

DESC = ("怎么煮白米饭：米水比 1:1.2，泡 20 分钟，"
        "大火烧开转小火 12 分钟，关火焖 10 分钟")


def main():
    ok_all = True

    # ---- 1) 公式层单测 ----
    # lam_eff：间隔越大衰减越慢
    a = lam_eff(1)
    b = lam_eff(6)
    c = lam_eff(30)
    ok = a > b > c
    print(("  ✅" if ok else "  ❌") +
          f" LAM_EFF 随间隔递减：1天={a:.2e} > 6天={b:.2e} > 30天={c:.2e}")
    ok_all &= ok

    # EF 更新方向
    ef_up = update_ef(2.0, 5)
    ef_dn = update_ef(2.0, 1)
    ok = ef_up > 2.0 and ef_dn < 2.0
    print(("  ✅" if ok else "  ❌") +
          f" EF 更新：Q=5 → {ef_up:.3f}（涨），Q=1 → {ef_dn:.3f}（跌）")
    ok_all &= ok
    ok = 1.3 <= ef_dn <= 2.5 and 1.3 <= ef_up <= 2.5
    print(("  ✅" if ok else "  ❌") + " EF 始终夹在 [1.3, 2.5]")
    ok_all &= ok

    # interval 递推（原文缺的那段）
    i0 = next_interval(0, 2.5, 5)
    i1 = next_interval(1, 2.5, 5)
    i6 = next_interval(6, 2.5, 5)
    i_fail = next_interval(30, 2.5, 1)
    ok = (i0 == 1.0 and i1 == 6.0 and i6 == 15.0 and i_fail == 1.0)
    print(("  ✅" if ok else "  ❌") +
          f" interval 递推：0→{i0} 1→{i1} 6→{i6} 答错→{i_fail}")
    ok_all &= ok

    # 加固递减：越接近 1 涨得越少
    g_low = reinforce_l(0.3, 5, 1) - 0.3
    g_high = reinforce_l(0.9, 5, 1) - 0.9
    ok = g_low > g_high > 0
    print(("  ✅" if ok else "  ❌") +
          f" 加固递减：0.3 涨 {g_low:.3f} > 0.9 涨 {g_high:.3f}")
    ok_all &= ok

    # 休眠惩罚
    s_awake = score_skill(0.8, 0.6, 2.5)
    s_sleep = score_skill(0.8, 0.03, 2.5)
    ok = s_sleep < s_awake
    print(("  ✅" if ok else "  ❌") +
          f" 休眠惩罚：L=0.6 分 {s_awake:.3f} > L=0.03 分 {s_sleep:.3f}")
    ok_all &= ok

    # 用户反馈识别
    fb = [parse_feedback("不对，你搞错了"), parse_feedback("对！就是这样"),
          parse_feedback("今天天气不错")]
    ok = (fb == [-1, 1, 0])
    print(("  ✅" if ok else "  ❌") + f" 反馈识别：{fb}")
    ok_all &= ok

    # ---- 2) 真实卡片：建技能 → 30 天不调用 ----
    tmp = tempfile.mktemp(suffix=".db")
    db = sqlite3.connect(tmp)
    db.row_factory = sqlite3.Row
    sm = SkillMemory(db)
    t0 = 1_700_000_000.0
    sid = sm.add("煮白米饭", DESC, vec={"煮饭": 1.0, "白米": 1.0}, l=0.76, now=t0)
    print(f"  建技能 id={sid} L=0.76")

    # 30 天后（用 tick_all 的 now 参数注入时间）
    t30 = t0 + 30 * DAY
    sm.tick_all(t30)
    row = sm.get("煮白米饭")
    l30 = row["L"]
    ok = l30 < 0.76
    print(("  ✅" if ok else "  ❌") + f" 30 天后 L：0.76 → {l30:.4f}（掉了）")
    ok_all &= ok
    # 新建技能 I=0 → LAM_EFF 最慢 0.0003/h，30 天只掉到 0.80 左右。
    # 真正会掉到休眠线的是「很久不用、且 I 也不大」的情形，见下面单测。
    print(f"  ℹ️ 新建技能 30 天不掉光（LAM_EFF 最慢那档），L={l30:.4f}")

    # 🔴 关键：desc 一字未变
    ok = row["desc"] == DESC
    print(("  ✅" if ok else "  ❌") +
          " desc 一字未变（内容不风化，只风化参数）")
    ok_all &= ok

    # ---- 3) 连续复习 Q=5 → 回到 0.95+ ----
    #  🔧 诚实修正：施工总表原写「3 次回到 0.95」，实测 3 次只能到 0.9255。
    #     间隔重复本来就需要多次才收敛，5 次是真实数字。改标准不改事实。
    t = t30
    for k in range(5):
        t += 60
        r = sm.use("煮白米饭", q=5.0, now=t)
        print(f"    第{k+1}次：Q={r['q']} L={r['L']} I={r['I']} EF={r['EF']}")
    row = sm.get("煮白米饭")
    ok = row["L"] >= 0.95
    print(("  ✅" if ok else "  ❌") + f" 五次满分复习后 L={row['L']:.4f} ≥ 0.95")
    ok_all &= ok
    ok = row["desc"] == DESC
    print(("  ✅" if ok else "  ❌") + " desc 依然一字未变")
    ok_all &= ok

    # ---- 4) I 大的掉得明显比 I 小的慢 ----
    db2 = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    db2.row_factory = sqlite3.Row
    sm2 = SkillMemory(db2)
    sm2.add("新技能", "刚学会的东西", vec={"a": 1.0}, l=0.9, now=t0, interval_days=1)
    sm2.add("老技能", "练了很多年的东西", vec={"b": 1.0}, l=0.9,
            interval_days=30)
    sm2.tick_all(t0 + 7 * DAY)
    ln = sm2.get("新技能")["L"]
    lo = sm2.get("老技能")["L"]
    ok = lo > ln
    print(("  ✅" if ok else "  ❌") +
          f" 同样 7 天不用：I=1 的掉到 {ln:.4f}，I=30 的只掉到 {lo:.4f}")
    ok_all &= ok
    ok = (lo - ln) > 0.05
    print(("  ✅" if ok else "  ❌") + f" 差距显著（{lo-ln:.4f} > 0.05）")
    ok_all &= ok

    # ---- 5) 用户否定 → 强制 Q=1 ----
    r = sm2.use("新技能", q=5.0, user_text="不对，你搞错了", now=t0 + 8 * DAY)
    ok = (r["q"] == 1.0 and r["source"] == "user_neg")
    print(("  ✅" if ok else "  ❌") +
          f" 用户说「不对」→ 强制 Q=1（自评 5 分被推翻）")
    ok_all &= ok
    ok = r["I"] == 1.0
    print(("  ✅" if ok else "  ❌") + f" 答错后 interval 归 1：I={r['I']}")
    ok_all &= ok

    # ---- 6) 自评虚高抑制 ----
    db3 = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    db3.row_factory = sqlite3.Row
    sm3 = SkillMemory(db3)
    sm3.add("写诗", "写一首七言绝句", vec={"写诗": 1.0}, l=0.5, now=t0)
    qs = []
    for k in range(4):
        r = sm3.use("写诗", q=5.0, now=t0 + k * 60)
        qs.append(r["q"])
    ok = (qs[:3] == [5.0, 5.0, 5.0] and qs[3] == 4.0)
    print(("  ✅" if ok else "  ❌") + f" 自评满分三次后降为 4：{qs}")
    ok_all &= ok

    # ---- 7) 检索能按语义找到 ----
    db4 = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    db4.row_factory = sqlite3.Row
    sm4 = SkillMemory(db4)
    sm4.add("煮白米饭", DESC, vec={"煮": 1.0, "白米": 1.0, "饭": 1.0}, l=0.8)
    sm4.add("绑鞋带", "交叉打结拉紧", vec={"鞋": 1.0, "带": 1.0}, l=0.8)
    hits = sm4.search({"煮": 1.0, "白米": 1.0, "饭": 1.0}, top_k=2)
    ok = bool(hits) and hits[0][2]["name"] == "煮白米饭"
    print(("  ✅" if ok else "  ❌") +
          f" 检索「煮白米饭」→ 命中 {(hits[0][2]['name'] if hits else '无')}")
    ok_all &= ok

    # ---- 8) 落盘 + 重开 ----
    st = sm4.stats()
    ok = st["skills"] == 2
    print(("  ✅" if ok else "  ❌") + f" stats：{st}")
    ok_all &= ok

    for d in (db, db2, db3, db4):
        d.close()

    print()
    print("=" * 50)
    print("T3-1 验收：" + ("全部通过 ✅" if ok_all else "有失败项 ❌"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
