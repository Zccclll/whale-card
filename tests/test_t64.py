# -*- coding: utf-8 -*-
"""T6-4 验收：记忆删除台账（mem_tomb）

为什么要有这张表：
    记忆会正常消失 —— 侵蚀到头、短期池到期没上榜、深层沉成统计、清晰度归零。
    可出生证（mem_birth）留在库里。没有台账的话，verify_birth 只能看见
    「有证没记录」，跟「有人把记录抽走了」长得一模一样 ——
    那就不是档案，那是猜。

验收标准：
    1. 走 _forget_row 删掉的记忆 → 台账里有一笔（含哈希、原因、时间）
    2. 这种删除后 verify_birth 仍然 ok，问题列表里写的是「已知删除」
    3. 绕开台账直接 DELETE → verify_birth 必须报「记忆被删过…台账里也没有」
    4. 记忆被偷改原文 → 仍然报「记忆被改过」（台账不能把篡改洗白）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("WHALE_EMBED_MODEL", "bge-small-zh-v1.5")

import card2
import timeline


def main():
    tmpdir = tempfile.mkdtemp()
    dbp = os.path.join(tmpdir, "card.db")
    c = card2.MemoryCard(dbp)
    tl = timeline.Timeline(c.db)
    c.tl = tl

    ok_all = True

    def check(cond, msg):
        nonlocal ok_all
        print(("  ✅" if cond else "  ❌") + " " + msg)
        ok_all &= bool(cond)

    # ---------- 1) 正常删除：先开证，再走 _forget_row ----------
    m1 = c.remember("用户周六下午去了镇上的旧书店", speaker="user")
    tl.birth_stamp(m1)
    ok, bad = tl.verify_birth()
    check(ok and not bad, "存档后 verify_birth ok=%s bad=%s" % (ok, bad))

    curt = c.db.cursor()
    c._forget_row(curt, m1, "测试：按规矩删掉的记忆")
    c.db.commit()

    tomb = tl.mem_tomb_list()
    check(m1 in tomb, "台账里有 id=%d" % m1)
    check(tomb.get(m1, {}).get("reason") == "测试：按规矩删掉的记忆",
          "台账记下了原因：%r" % (tomb.get(m1, {}).get("reason"),))
    check(len(tomb.get(m1, {}).get("hash") or "") == 64,
          "台账记下了出生证哈希（删了就算不出来了）")
    ok, bad = tl.verify_birth()
    check(ok, "按规矩删掉后 verify_birth 仍 ok")
    check(any("已知删除" in b and str(m1) in b for b in bad),
          "问题列表写的是「已知删除」而不是「被删过」：%s" % bad)

    # ---------- 2) 绕开台账直接删：必须报出来 ----------
    m2 = c.remember("用户把台灯换成了暖黄的灯泡", speaker="user")
    tl.birth_stamp(m2)
    c.db.execute("DELETE FROM mem WHERE id=?", (m2,))
    c.db.commit()
    ok, bad = tl.verify_birth()
    check(not ok, "绕开台账直接删后 verify_birth 必须不 ok")
    check(any(("记忆被删过" in b and str(m2) in b) for b in bad),
          "报出了「有出生证但没有记录，台账里也没有」：%s" % bad)

    # ---------- 3) 偷改原文：台账管不着，必须照样报 ----------
    m3 = c.remember("用户说下周要交物理实验报告", speaker="user")
    tl.birth_stamp(m3)
    c.db.execute("UPDATE mem SET raw=? WHERE id=?",
                 ("用户说下周不用交物理实验报告", m3))
    c.db.commit()
    ok, bad = tl.verify_birth()
    check(not ok, "偷改原文后 verify_birth 必须不 ok")
    check(any(("记忆被改过" in b and str(m3) in b) for b in bad),
          "报出了「出生证对不上」：%s" % bad)

    # ---------- 4) 台账数量对得上：删几条就有几条 ----------
    n_before = len(tl.mem_tomb_list())
    m4 = c.remember("用户说他想养一只猫", speaker="user")
    c._forget_row(c.db.cursor(), m4, "测试：再删一条")
    c.db.commit()
    check(len(tl.mem_tomb_list()) == n_before + 1,
          "每删一条台账多一笔（%d → %d）" % (n_before, len(tl.mem_tomb_list())))

    c.db.close()
    print("\n结果：" + ("全过 ✅" if ok_all else "有失败 ❌"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
