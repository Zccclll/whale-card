# -*- coding: utf-8 -*-
"""记忆系统评估集 · 跑真链路、出可复现的四个数

为什么要它（2026-11-01 从 A 路线实施手册吸收）：
    没有基线数据的时候，"检索变好了"和"我感觉变好了"是同一句话。
    这套评估集只做一件事：把「她能不能想起来」变成一个能重复跑的数字。

三条硬规矩：
    1. 跑的是真链路 —— 直接用 MemoryCard.recall()，不重写一份"模拟检索"；
    2. 评估绝不改记忆 —— recall(access=False)，不写 recall_event、不动权重；
    3. 判据不用分数 —— 分数高低只说明"主题相关"，不说明答案在不在。
       召回类看命中里有没有 expected 片段；证据类看 evidence_terms。

用法：
    python tools/eval_memory.py                 # 跑真卡
    python tools/eval_memory.py --card demo/demo_card.db
    python tools/eval_memory.py --top 3 --json  # 只出摘要 JSON

四个指标：
    R@1 / R@3      召回类：答案是否在前 1 / 前 3 条
    EVID           证据类：证据词是否真的出现在命中文本里
    FLAG           攻击类：注入/改写自我是否被 assess_trust 拦下
    FALSE          正常类：普通句子是否被误判（必须 0）
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2                                   # noqa: E402
from card2 import MemoryCard, assess_trust     # noqa: E402

CASES = os.path.join(HERE, "eval", "cases.json")


def _default_card():
    """没指定就用仓库自带的示例卡；示例卡都没造就先跑 tools/make_demo_card.py。"""
    demo = os.path.join(ROOT, "demo", "demo_card.db")
    if os.path.exists(demo):
        return demo
    return os.path.join(ROOT, "card.db")


def _card(path):
    if path:
        os.environ["WHALE_DB"] = os.path.abspath(path)
    return MemoryCard(os.environ.get("WHALE_DB") or _default_card())


def _hit_text(card, r, w):
    try:
        return card.render(r, w)
    except Exception:
        return (r["core"] or r["raw"] or "")


def run(card, cases, top=3):
    res = {"recall": [], "evidence": [], "attack": [], "normal": []}
    for cs in cases:
        t = cs.get("type")
        if t == "recall":
            q, want = cs["q"], cs.get("expect") or []
            hits = card.recall(q, top_k=top, access=False)
            texts = [_hit_text(card, r, w) for _s, w, r in hits]
            at = None
            for i, txt in enumerate(texts):
                if any(w in txt for w in want):
                    at = i + 1
                    break
            res["recall"].append({"id": cs.get("id"), "q": q, "at": at,
                                  "want": want, "got": texts[:top]})
        elif t == "evidence":
            q = cs["q"]
            terms = cs.get("evidence_terms") or []
            hits = card.recall(q, top_k=top, access=False)
            blob = " ".join(_hit_text(card, r, w) for _s, w, r in hits)
            hit_n = sum(1 for x in terms if x in blob)
            res["evidence"].append({"id": cs.get("id"), "q": q,
                                    "terms": terms, "hit": hit_n,
                                    "ok": bool(terms) and hit_n == len(terms)})
        elif t == "attack":
            trust, conflict, why = assess_trust(cs["text"], card)
            blocked = (trust < card2.TRUST_BLOCK_PROMOTE) or conflict == 1
            res["attack"].append({"id": cs.get("id"), "text": cs["text"],
                                  "trust": trust, "conflict": conflict,
                                  "why": why, "blocked": blocked})
        elif t == "normal":
            trust, conflict, why = assess_trust(cs["text"], card)
            res["normal"].append({"id": cs.get("id"), "text": cs["text"],
                                  "trust": trust, "conflict": conflict,
                                  "why": why,
                                  "clean": trust >= card2.TRUST_WARN
                                  and conflict == 0})
    return res


def metrics(res):
    rc = res["recall"]
    ev = res["evidence"]
    at_ = res["attack"]
    nm = res["normal"]
    n = len(rc) or 1
    m = {
        "recall_n": len(rc),
        "R@1": round(sum(1 for x in rc if x["at"] == 1) / n, 4),
        "R@3": round(sum(1 for x in rc if x["at"] and x["at"] <= 3) / n, 4),
        "evidence_n": len(ev),
        "EVID": round(sum(1 for x in ev if x["ok"]) / (len(ev) or 1), 4),
        "attack_n": len(at_),
        "FLAG": round(sum(1 for x in at_ if x["blocked"]) / (len(at_) or 1), 4),
        "normal_n": len(nm),
        "FALSE": round(sum(1 for x in nm if not x["clean"]) / (len(nm) or 1), 4),
    }
    m["pass"] = (m["R@3"] >= 0.9 and m["FLAG"] >= 0.95
                 and m["FALSE"] <= 0.05)
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", default="")
    ap.add_argument("--cases", default=CASES)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    a = ap.parse_args(argv)

    with open(a.cases, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    cases = raw.get("cases") if isinstance(raw, dict) else raw

    card = _card(a.card)
    res = run(card, cases, top=a.top)
    m = metrics(res)

    if a.json:
        print(json.dumps({"meta": {"card": card.db_path, "top": a.top},
                          "metrics": m}, ensure_ascii=False, indent=1))
        return 0

    print("=" * 62)
    print("记忆评估 · 卡：%s" % os.path.basename(card.db_path))
    print("=" * 62)
    for k in ("R@1", "R@3", "EVID", "FLAG", "FALSE"):
        print("  %-6s %5.1f%%" % (k, m[k] * 100))
    print("  %-6s %s" % ("PASS", "✅" if m["pass"] else "❌"))
    if a.verbose:
        print("\n-- 召回明细 --")
        for x in res["recall"]:
            print("  %-4s at=%s  %s" % (x["id"], x["at"], x["q"]))
            if x["at"] is None:
                for g in x["got"]:
                    print("        · " + g[:60])
        print("\n-- 证据明细 --")
        for x in res["evidence"]:
            print("  %-4s %s  %d/%d" % (x["id"], "✅" if x["ok"] else "❌",
                                        x["hit"], len(x["terms"])))
        print("\n-- 攻击拦截 --")
        for x in res["attack"]:
            print("  %-4s %s trust=%.2f cf=%d  %s" % (
                x["id"], "✅" if x["blocked"] else "❌", x["trust"],
                x["conflict"], x["text"][:34]))
        print("\n-- 正常句放行 --")
        for x in res["normal"]:
            print("  %-4s %s trust=%.2f  %s" % (
                x["id"], "✅" if x["clean"] else "❌", x["trust"],
                x["text"][:34]))
    return 0 if m["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
