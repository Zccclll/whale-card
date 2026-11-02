# -*- coding: utf-8 -*-
"""五层记忆迁移（2026-11-01）。

做什么：
  1) 给 card.db 的 mem 表补 tier / subject_key / history / valid_until /
     recall_n / t_last_recall 六列（MemoryCard.__init__ 里已经会补，这里只是跑一遍）；
  2) 按分区 + 关键词把"该进底色的"挑出来，**先生成清单给你看**；
  3) 加 --apply 才真的写库（写之前已经备份过 card.db）。

用法：
    python _migrate_tiers.py            # 只看清单（dry run）
    python _migrate_tiers.py --apply    # 真写
"""
import json
import re
import sys
import time

import card2

DB = r"whale-card\card.db"

# 处境类：会过期，但不该忘
SITUATION_RE = re.compile(r"高三|高中|在读|上高中|考大学|高考|住校|宿舍|班级|年级")
# 关键人物：先只认这几个，别乱猜
PEOPLE = [("使用者", "最重要的人", "core")]
# 分区 → 底色种类
REGION_KIND = {
    "preference": ("taste", None),
    "habit": ("habit", None),
    "self": ("value", None),
    "flash": ("value", None),
}


def main():
    apply = "--apply" in sys.argv
    card = card2.MemoryCard(DB)
    now = time.time()
    c = card.db.cursor()
    rows = c.execute("SELECT * FROM mem").fetchall()
    plan = {"core_fixed": [], "core_tag": [], "core_mutable": [],
            "stay_mid": [], "expire": []}

    # 底色只收"关于我/关于你"的短句，长对话、系统消息、代码一律不进
    JUNK = re.compile(r"dsh|mnemon|prompt|http|```|\x00")
    PREF_HINT = re.compile(r"喜欢|讨厌|爱吃|爱喝|受不了|看不惯|习惯|我怕冷|怕热|不吃|能吃")

    for r in rows:
        raw = (r["raw"] or "")
        region = r["region"] or "episode"
        tier = r["tier"] if "tier" in r.keys() else "mid"
        if tier != "mid":
            continue
        if JUNK.search(raw) or len(raw) > 120:
            plan["stay_mid"].append(r["id"])
            continue
        hit = None
        # ① 处境
        if SITUATION_RE.search(raw):
            hit = ("core_mutable", "situation", "situation:" + raw[:12])
            plan["expire"].append(r["id"])
        # ② 人物标签
        if hit is None:
            for name, role, layer in PEOPLE:
                if name in raw:
                    hit = ("core_tag", "role", "role:" + name)
                    break
        # ③ 分区（只收明确的偏好/习惯，以及真的在说"我喜欢/我讨厌"的）
        if hit is None and (region in ("preference", "habit")
                            or PREF_HINT.search(raw)):
            kind = REGION_KIND.get(region, ("taste", None))[0]
            key = "%s:%s" % (kind, raw[:10])
            hit = ("core_mutable", kind, key)
        if hit is None:
            plan["stay_mid"].append(r["id"])
            continue
        tier_t, kind, key = hit
        plan[tier_t].append({"id": r["id"], "kw": key, "raw": raw[:40],
                             "w": round(r["w"], 3),
                             "n": r["recall_n"] if "recall_n" in r.keys() else 0})

    print("=== 迁移清单（%d 条）===" % len(rows))
    for k in ("core_fixed", "core_tag", "core_mutable"):
        print("\n[%s] %d 条" % (k, len(plan[k])))
        for it in plan[k][:12]:
            print("  #%-4s w=%.2f n=%s  %s" % (it["id"], it["w"], it["n"],
                                               it["raw"].replace("\n", " ")))
        if len(plan[k]) > 12:
            print("  … 还有 %d 条" % (len(plan[k]) - 12))
    print("\n[留在中期] %d 条" % len(plan["stay_mid"]))
    print("[处境带截止日期] %d 条（2027-06-10 高考后自动降级）" %
          len(plan["expire"]))

    if not apply:
        print("\n（dry run：没写库。要写就加 --apply）")
        return 0

    # 真写
    valid_until = time.mktime(time.strptime("2027-06-11", "%Y-%m-%d"))
    n = 0
    for it in plan["core_mutable"]:
        kind = it["kw"].split(":")[0]
        card.set_tier(it["id"], card2.TIER_CORE_MUTABLE, kind=kind,
                      subject_key=it["kw"])
        if it["id"] in plan["expire"]:
            c.execute("UPDATE mem SET valid_until=? WHERE id=?",
                      (valid_until, it["id"]))
        n += 1
    for it in plan["core_tag"]:
        card.set_tier(it["id"], card2.TIER_CORE_TAG, kind="role",
                      subject_key=it["kw"])
        n += 1
    card.db.commit()
    print("\n已写库：%d 条进底色/标签" % n)
    print("分层结果：", json.dumps(card.core_stats(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
