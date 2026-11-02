# -*- coding: utf-8 -*-
"""心智服务端到端验收（T6-1 的真实验收，不靠"看起来能跑"）

为什么单独写一个：
    施工总表上 T6-1 早就打了 ✅，但今天第一次真启动时立刻撞上
    `TypeError: sequence item 0: expected str instance, dict found`。
    "写完了"和"跑得通"是两件事，这个脚本专门验第二件。

用法（服务要先起来）：
    python mind_server.py --port 8848 --db card.db &
    python verify_mind.py                    # 默认 127.0.0.1:8848
    python verify_mind.py --port 8848
"""
import argparse
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8848"
PASS, FAIL = [], []


def call(path, payload=None, timeout=90):
    url = BASE + path
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  ✅ %s" % name)
    else:
        FAIL.append((name, detail))
        print("  ❌ %s   %s" % (name, detail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8848)
    a = ap.parse_args()
    global BASE
    BASE = "http://127.0.0.1:%d" % a.port

    print("=" * 60)
    print("心智服务端到端验收")
    print("=" * 60)

    # ---- 1. 存活 ----
    print("\n[1] /health 存活 + 卡状态")
    try:
        h = call("/health")
    except Exception as e:
        print("  ❌ 连不上服务：%s" % e)
        print("\n结论：服务没起来，后面的都不用测了。")
        return 1
    check("ok=true", h.get("ok") is True)
    check("记忆条数 > 0", int(h.get("memories") or 0) > 0,
          "memories=%s" % h.get("memories"))
    check("code=0 → 建议先跑 /seal 盖章",
          h.get("sealed") is not None, "sealed=%s" % h.get("sealed"))

    # ---- 2. recent 两种形状都要能接 ----
    print("\n[2] /context 的 recent 形状兼容（今天崩过的地方）")
    for label, recent in [
        ("recent=[str,...]", ["早啊", "吃饭了没"]),
        ("recent=[dict,...]", [{"role": "user", "text": "早啊"},
                               {"role": "assistant", "text": "吃饭了没"}]),
        ("recent 缺字段", [{"role": "user"}, {"text": "只有text"}]),
        ("recent 为空", []),
        ("recent 不给", None),
    ]:
        payload = {"message": "你还记得我最爱吃什么吗"}
        if recent is not None:
            payload["recent"] = recent
        try:
            r = call("/context", payload)
            ctx = r.get("context") or {}
            okr = bool(r.get("ok")) and bool(ctx.get("prompt"))
            check(label, okr, "" if okr else "prompt 为空或 ok=false")
        except Exception as e:
            check(label, False, str(e)[:120])

    # ---- 3. 组装内容是否真的有用 ----
    print("\n[3] 组装出来的上下文有没有货")
    r = call("/context", {"message": "你还记得我最爱吃什么吗"})
    ctx = r.get("context") or {}
    prompt = ctx.get("prompt") or ""
    check("prompt 非空", len(prompt) > 100, "len=%d" % len(prompt))
    check("含【我一直记得的事】", "我一直记得的事" in prompt)
    check("含【这句话让我想起了】", "这句话让我想起了" in prompt)
    check("翻出了白米饭", "白米饭" in prompt,
          "没翻出来就说明召回没工作")
    check("est_tokens 合理(0<tok<50000)",
          0 < int(ctx.get("est_tokens") or 0) < 50000,
          "est_tokens=%s" % ctx.get("est_tokens"))
    w = ctx.get("weights_named")
    check("weights_named 是 6 维且和≈1",
          isinstance(w, dict) and len(w) == 6 and
          abs(sum(float(x) for x in w.values()) - 1.0) < 0.05,
          "weights_named=%s" % w)

    # ---- 4. 失败必须是失败，不能装作没事 ----
    print("\n[4] 失败要明确报错（不许返回看起来还行的空上下文）")
    try:
        call("/context", {"message": ""})
        check("空 message 应当报错", False, "居然返回成功")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        check("空 message 返回 4xx/500 且带 error 字段",
              e.code >= 400 and "error" in body, "code=%s" % e.code)
    except Exception as e:
        check("空 message 报错", False, str(e)[:120])

    # ---- 5. 记账 ----
    print("\n[5] /report 记下一轮")
    try:
        r = call("/report", {"user_message": "验收测试：今天几号了",
                             "reply": "验收测试回复"})
        check("/report ok", bool(r.get("ok")), json.dumps(r, ensure_ascii=False)[:150])
    except Exception as e:
        check("/report ok", False, str(e)[:150])

    # ---- 汇总 ----
    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("\n失败清单：")
        for n, d in FAIL:
            print("  · %s   %s" % (n, d))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
