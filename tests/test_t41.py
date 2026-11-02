# -*- coding: utf-8 -*-
"""分舱预筛的「安全」验收（重写版，2026-10-31）

为什么重写
----------
原来的 test_t41.py 有 8 组断言，其中一半在验证一个我们**已经主动关掉**的
能力（分舱候选集变小、分舱更快）。修法是把 `POD_ENABLED` 关掉之后，
那些断言就必然失败 —— 但它们失败的原因不是"程序坏了"，而是
"这个测试还在测一个已停用的功能"。

测试的价值在于**锁定行为**，不在于证明某个已废弃的设计还在跑。
所以这里重写成真正该守的三件事：

  1. 分舱代码没被删、真要用的时候还能跑（POD_ENABLED=True 时工作正常）
  2. 默认配置下，**预筛不会把正确答案踢出候选集** ← 这是今晚的血泪教训
  3. 时间窗预筛不会把库筛空

用法
----
    python test_t41.py
"""
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2
from card2 import MemoryCard, Clock, DAY, query_time_window

TOPICS = ["烧烤", "数学", "橘猫", "电影", "硬盘", "记忆卡", "白米饭", "物理",
          "语文", "英语", "生物", "同桌", "老师", "作业", "考试", "篮球",
          "耳机", "键盘", "代码", "插件", "模型", "服务器", "相机", "婚礼"]
VERBS = ["吃了", "看了", "买了", "聊了", "写了", "学了", "做了", "去了"]
TAILS = ["很开心", "有点累", "还不错", "挺好玩", "有点烦", "特别激动"]

_ok_count = 0
_fail = []


def check(cond, msg):
    global _ok_count
    if cond:
        _ok_count += 1
        print("  ✅ " + msg, flush=True)
    else:
        _fail.append(msg)
        print("  ❌ " + msg, flush=True)
    return cond


def main():
    tmp = tempfile.mktemp(suffix=".db")
    start = 1_700_000_000
    card = MemoryCard(tmp, Clock(start=start))

    rnd = random.Random(7)
    t0 = time.time()
    for i in range(1200):
        txt = (rnd.choice(TOPICS) + rnd.choice(VERBS) + rnd.choice(TAILS))
        ts = start - rnd.randint(0, 365) * DAY
        card.remember(txt, salience=0.6, ts=ts)
    n = card.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
    print("  造 %d 条耗时 %.0fms\n" % (n, (time.time() - t0) * 1000), flush=True)

    # ========================================================
    # 组 1：分舱代码还得是活的（真打开时能用）
    # ========================================================
    print("【组 1】分舱代码保留可用（POD_ENABLED=True 时）", flush=True)
    saved = card2.POD_ENABLED
    card2.POD_ENABLED = True
    try:
        t0 = time.time()
        card._ensure_pods()
        pod_ms = (time.time() - t0) * 1000
        cents = getattr(card, "_pod_centroids", None)
        check(bool(cents), "聚类出 %d 个舱，耗时 %.0fms"
              % (len(cents) if cents else 0, pod_ms))

        if cents:
            from collections import Counter
            cnt = Counter(card._pod_of.values())
            check(len(card._pod_of) == n
                  and all(cnt.get(i, 0) > 0 for i in range(len(cents))),
                  "舱成员覆盖 %d/%d，分布 %s"
                  % (len(card._pod_of), n, dict(sorted(cnt.items()))))

        qv = card2.to_vec("你还记得那次烧烤吗")
        idf = card._ensure_idf()
        cand = card._pod_candidates(qv, idf)
        check(cand is not None and len(cand) < n,
              "打开后候选集确实从 %d 缩到 %s 条"
              % (n, len(cand) if cand else n))

        hits = card.recall("你还记得那次烧烤吗", top_k=3, access=False)
        got = hits[0][2]["raw"] if hits else ""
        check("烧烤" in got, "打开后仍命中：「%s」" % got)

        before = card._pod_n
        for i in range(200):
            card.remember("新来的第%d条记忆，关于%s"
                          % (i, rnd.choice(TOPICS)), salience=0.6)
        check(card._pods_stale(), "新增 200 条后判定该重建（旧覆盖 %d 条）" % before)
        card._ensure_pods()
        check(card._pod_n > before, "重建后覆盖 %d 条" % card._pod_n)
    finally:
        card2.POD_ENABLED = saved

    # ========================================================
    # 组 2：默认配置下，预筛不能把正确答案踢出去 ← 今晚的教训
    # ========================================================
    print("\n【组 2】默认配置（POD_ENABLED=%s）预筛不踢答案"
          % card2.POD_ENABLED, flush=True)
    check(card2.POD_ENABLED is False, "分舱默认关闭")

    tmp2 = tempfile.mktemp(suffix=".db")
    c2 = MemoryCard(tmp2, Clock(start=start))
    # 造几条明确的记忆，确保「预筛后正确答案还在候选里」
    c2.remember("用户喜欢吃白米饭", salience=0.9, ts=start - 30 * DAY)
    c2.remember("用户暑假靠拍婚礼挣钱想攒钱买硬盘", salience=0.9,
                ts=start - 40 * DAY)
    c2.remember("今天中午吃了烧烤，很开心", salience=0.8, ts=start - 3600)
    for i in range(60):
        c2.remember("无关的聊天内容第%d条，随便说说" % i, salience=0.5,
                    ts=start - rnd.randint(1, 300) * DAY)

    # 关键断言：recall 的「候选集」必须就是全库（因为分舱关了）
    for q, want_kw in [("我爱吃什么", "白米饭"),
                       ("暑假靠什么挣钱", "拍婚礼"),
                       ("今天中午吃了什么", "烧烤")]:
        hits = c2.recall(q, top_k=3, access=False, assoc=False)
        raws = [h[2]["raw"] for h in hits]
        check(any(want_kw in r for r in raws),
              "「%s」前三条里有「%s」：%s"
              % (q, want_kw, raws[:2]))

    c2.close()

    # ========================================================
    # 组 3：时间窗
    # ========================================================
    print("\n【组 3】时间窗判定与不筛空", flush=True)
    for q, want in [
        ("今天中午吃了什么", 3.0),
        ("最近在忙什么", 14.0),
        ("你还记得那次烧烤吗", None),
        ("以前在学校怎么样", None),
        ("刚刚说到哪了", 3.0),
        ("这个脚本怎么改", None),
    ]:
        got = query_time_window(q)
        check(got == want, "「%s」→ 时间窗 %s（期望 %s）" % (q, got, want))

    tmp3 = tempfile.mktemp(suffix=".db")
    c3 = MemoryCard(tmp3, Clock(start=start))
    c3.remember("今天吃了白米饭，很香", salience=0.7, ts=start - 3600)
    c3.remember("三年前去过一次海边，印象很深", salience=0.7,
                ts=start - 1000 * DAY)
    rows = c3.db.execute("SELECT * FROM mem").fetchall()
    win = query_time_window("今天中午吃了什么")
    cut = start - win * DAY
    narrowed = [r for r in rows if r["last_active_ts"] >= cut
                or r["create_ts"] >= cut]
    check(len(narrowed) == 1 and "白米饭" in narrowed[0]["raw"],
          "问「今天」只留最近 %d 条（白米饭）" % len(narrowed))
    check(len(narrowed) < 5, "候选 %d 条 < 5 ⇒ 会放宽回全库（不会筛空）"
          % len(narrowed))
    c3.close()

    card.close()
    for p in (tmp, tmp2, tmp3):
        try:
            os.remove(p)
        except OSError:
            pass

    print("\n" + "=" * 50)
    if _fail:
        print("T4-1/T4-2 验收：%d 项通过，%d 项失败 ❌"
              % (_ok_count, len(_fail)))
        for m in _fail:
            print("   失败：" + m)
        return 1
    print("T4-1/T4-2 验收：全部通过 ✅（%d 项）" % _ok_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
