# -*- coding: utf-8 -*-
"""T3-3 六维动态上下文权重（V5.0 §10）

为什么要有这一层：
    V4.1 把四格预算写死成 600 / 1800 / 2000 / 1200。
    结果是聊技术的时候人格格占着 600 没用上，
    执行技能的时候技能格只有 1200 不够用 —— 静态预算 = 呆板。

    这一层做的事只有一件：**总长固定，配额流动**。
    六个维度按「现在在聊什么 + 我现在什么状态」动态分那 6000 token。

六维（V5.0 §10.2）：
    A 身份内核（人格 + 身份锚 + 底线）     永不裁剪，A >= 0.15
    B 自我认知（我是谁、性格、处境）       半稳定
    C 短时记忆（最近 N 条原始对话）         高频变动
    D 陈述记忆（长期池的事实/偏好/习惯）    低频变动
    E 印象记忆（对他的看法、依恋）          慢变动
    F 技能记忆（命中的程序性记忆）          按需

四格映射（V5.0 §10.5）：
    格 1【我是谁】           = A + B
    格 2【我一直记得的事】   = D
    格 3【我们最近在聊】     = C
    格 4【这句话让我想起了】 = E + F

    为什么 E、F 合并：两者都是「本轮被激活的东西」，
    一个是「想起了对他的看法」，一个是「想起了怎么做事」，
    放同一格里模型读起来是一段连续的「我此刻想到的」。
"""

# ---- 六维下标 ----
A, B, C, D, E, F = 0, 1, 2, 3, 4, 5
DIM_NAMES = ["身份内核", "自我认知", "短时记忆", "陈述记忆", "印象记忆", "技能记忆"]

A_MIN = 0.15          # A 的硬保底（V5.0 §10.2）
CELL_MIN = 150        # 每格最低 token（V5.0 §10.4 第 4 步）

# ---- 七张场景权重表（V5.0 §10.3）----
SCENE_WEIGHTS = {
    "普通对话": [0.20, 0.18, 0.22, 0.15, 0.15, 0.10],
    "情绪陪伴": [0.18, 0.16, 0.26, 0.10, 0.24, 0.06],
    "图像观察": [0.18, 0.15, 0.30, 0.12, 0.15, 0.10],
    "技能执行": [0.15, 0.10, 0.10, 0.15, 0.10, 0.40],
    "事实查询": [0.16, 0.12, 0.14, 0.34, 0.10, 0.14],
    "深度回忆": [0.18, 0.14, 0.18, 0.22, 0.20, 0.08],
    "冲突争执": [0.22, 0.20, 0.24, 0.08, 0.20, 0.06],
}

# ---- 运行时热改（V5.0 §10.9）----
#
# 为什么要有这一层：
#     这张场景权重表是**调参**的东西 —— 一定得能现场试、现场改。
#     写死在代码里，改一个数就要重启服务，那等于没人会去改它。
#
# 存在哪：卡里的 runtime_config 表（跟记忆同一个库，拔卡跟着走）。
#     为什么不放 config.json：多一个文件就多一处能跟库不同步的地方，
#     而「拔卡带走」这件事只有一个真值源才不会出错。
#
# 怎么生效：每轮 compute() 先读一行 `_rev`（单行主键查询，微秒级），
#     跟缓存里的 rev 不一样就重载全表。所以改完**下一轮立即生效**，不用重启。
RC_TABLE = "runtime_config"
RC_REV_KEY = "_rev"

# 英文别名 → 中文场景名（文档 §10.9 举的例子用的是 emotion 这种写法）
SCENE_ALIAS = {
    "normal": "普通对话", "plain": "普通对话", "chat": "普通对话",
    "emotion": "情绪陪伴", "emo": "情绪陪伴", "comfort": "情绪陪伴",
    "image": "图像观察", "vision": "图像观察",
    "skill": "技能执行", "exec": "技能执行",
    "fact": "事实查询", "query": "事实查询",
    "recall": "深度回忆", "memory": "深度回忆",
    "conflict": "冲突争执", "quarrel": "冲突争执",
}

_DEFAULT_CFG = {
    "scene_weights": dict((k, list(v)) for k, v in SCENE_WEIGHTS.items()),
    "a_min": A_MIN,
    "cell_min": CELL_MIN,
    "bad": [],          # 解析失败的键（写错了要能看见，不能静默用默认值）
}

# 缓存：id(db) → (rev, cfg, db 强引用)
# 强引用是为了不让 id() 被回收后重用 —— 两个不同的库撞同一个 id 会读到别人的配置。
_CFG_CACHE = {}


def _parse_six(s):
    """把 "0.18,0.16,0.26,0.10,0.24,0.06" 解析成 6 个非负 float。

    不合格就返回 None（调用方保留默认值并记进 bad）—— 宁可这一项没改，
    也不能让一个手滑的逗号把某一维权重变成 0 然后整格消失。
    """
    try:
        parts = [x for x in str(s).replace("，", ",").split(",") if x.strip()]
        if len(parts) != 6:
            return None
        vals = [float(x) for x in parts]
        if any(v < 0 or v != v or v > 1.0 for v in vals):
            return None
        if sum(vals) <= 0:
            return None
        return vals
    except Exception:
        return None


def ensure_table(db):
    """建 runtime_config 表（老库没有就补）。"""
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS runtime_config("
            " key TEXT PRIMARY KEY, val TEXT NOT NULL,"
            " ts REAL NOT NULL DEFAULT 0)")
        db.commit()
        return True
    except Exception:
        return False


def rc_set(db, key, val, ts=None):
    """写一条配置并 bump `_rev`（这样下一轮 compute 就会重载）。"""
    import time as _time
    t = float(ts if ts is not None else _time.time())
    c = db.cursor()
    c.execute("INSERT INTO runtime_config(key,val,ts) VALUES(?,?,?)"
              " ON CONFLICT(key) DO UPDATE SET val=excluded.val, ts=excluded.ts",
              (str(key), str(val), t))
    c.execute("INSERT INTO runtime_config(key,val,ts) VALUES(?,?,?)"
              " ON CONFLICT(key) DO UPDATE SET val=excluded.val, ts=excluded.ts",
              (RC_REV_KEY, "%.6f" % t, t))
    db.commit()
    return t


def rc_all(db):
    """把整张表读成 dict（不含 _rev）。"""
    out = {}
    try:
        for r in db.execute("SELECT key, val FROM runtime_config"):
            out[str(r[0])] = str(r[1])
    except Exception:
        pass
    out.pop(RC_REV_KEY, None)
    return out


def load_config(db=None, force=False):
    """读运行时配置；没有库或读不了就返回默认值。

    每次只多一条 `_rev` 主键查询。rev 没变就直接吃缓存。
    """
    if db is None:
        return _DEFAULT_CFG
    try:
        row = db.execute("SELECT val FROM runtime_config WHERE key=?",
                         (RC_REV_KEY,)).fetchone()
    except Exception:
        return _DEFAULT_CFG          # 老库还没这张表
    rev = str(row[0]) if row is not None else ""
    hit = _CFG_CACHE.get(id(db))
    if hit is not None and hit[0] == rev and not force:
        return hit[1]

    cfg = {
        "scene_weights": dict((k, list(v)) for k, v in SCENE_WEIGHTS.items()),
        "a_min": A_MIN,
        "cell_min": CELL_MIN,
        "bad": [],
    }
    for key, val in rc_all(db).items():
        k = key.strip()
        if k.startswith("scene_weight:"):
            name = k.split(":", 1)[1].strip()
            name = SCENE_ALIAS.get(name.lower(), name)
            six = _parse_six(val)
            if six is None or name not in SCENE_WEIGHTS:
                cfg["bad"].append("%s=%s" % (key, val))
                continue
            cfg["scene_weights"][name] = six
        elif k == "A_MIN":
            try:
                cfg["a_min"] = max(0.0, float(val))
            except Exception:
                cfg["bad"].append("%s=%s" % (key, val))
        elif k == "CELL_MIN":
            try:
                cfg["cell_min"] = max(0, int(float(val)))
            except Exception:
                cfg["bad"].append("%s=%s" % (key, val))
        # 认不出的键不报错也不生效 —— 留着给以后扩展用
    _CFG_CACHE[id(db)] = (rev, cfg, db)
    return cfg


# ---- 场景判定关键词 ----
import re

_RE_EMO = re.compile(
    r"难受|难过|想哭|哭|委屈|烦|累|孤单|害怕|焦虑|崩溃|压力|"
    r"开心|高兴|喜欢|爱你|想你|抱抱|陪你|陪陪")
_RE_SKILL = re.compile(
    r"怎么做|如何|步骤|操作|帮我做|帮我改|写个|写一个|生成|"
    r"命令|代码|脚本|配置|安装|运行|跑一下|调试|报错|修复")
_RE_FACT = re.compile(
    r"是多少|是什么|叫什么|哪个|什么时候|多少号|QQ号|手机号|"
    r"生日|几点|几天|多久|在哪|在哪个")
_RE_RECALL = re.compile(
    r"还记得|记得吗|以前|那次|那天|当时|曾经|上一次|之前|"
    r"回忆|想起来|说过")
_RE_CONFLICT = re.compile(
    r"你错了|不对|你根本|凭什么|闭嘴|烦人|讨厌你|你骗|敷衍|"
    r"生气|吵架|失望|为什么不")

# ---- 意图修饰（V5.0 §10.4 第 2 条）----
INTENT_RULES = [
    (_RE_RECALL, {D: 1.30, C: 0.85}),        # 问往事 → 多装旧事
    (re.compile(r"你现在|刚刚|刚才|现在"), {C: 1.30}),
    (re.compile(r"你觉得|你怎么看|你认为"), {E: 1.25}),
    (re.compile(r"你会不会|能不能|可不可以"), {F: 1.20}),
]


def classify_scene(query, recent_lines=None):
    """判断现在属于哪个场景。冲突 > 情绪 > 回忆 > 技能 > 事实 > 普通。

    顺序是有意的：吵架时就算问的是「还记得吗」，也该先保人格和底线。
    """
    q = query or ""
    if _RE_CONFLICT.search(q):
        return "冲突争执"
    if _RE_EMO.search(q):
        return "情绪陪伴"
    if _RE_RECALL.search(q):
        return "深度回忆"
    if _RE_SKILL.search(q):
        return "技能执行"
    if _RE_FACT.search(q):
        return "事实查询"
    return "普通对话"


def base_weights(scene, cfg=None):
    cfg = cfg or _DEFAULT_CFG
    table = cfg.get("scene_weights") or SCENE_WEIGHTS
    return list(table.get(scene, table.get("普通对话", SCENE_WEIGHTS["普通对话"])))


def apply_hormones(v, h):
    """内分泌修饰（V5.0 §10.4 第 1 条）

    只改「装多少」，不改「装什么」—— 这是防模型编造的一道闸。
    """
    v = list(v)
    if not h:
        return v
    if h.get("h_oxy", 0.5) > 0.7:
        v[E] *= 1.25
    if h.get("h_cort", 0.2) > 0.5:
        v[D] *= 0.80
    if h.get("h_dopa", 0.4) > 0.7:
        v[C] *= 1.15
    if h.get("h_sero", 0.6) > 0.7:
        v[B] *= 1.10
    return v


def apply_intent(v, query):
    v = list(v)
    q = query or ""
    for rx, deltas in INTENT_RULES:
        if rx.search(q):
            for idx, mul in deltas.items():
                v[idx] *= mul
    return v


def apply_session(v, turns):
    """会话长度修饰（V5.0 §10.4 第 3 条）"""
    v = list(v)
    if turns is not None:
        if turns < 3:
            v[C] *= 1.15
        elif turns > 20:
            v[C] *= 0.90
    return v


def apply_idle(v, idle_seconds):
    """空闲时长修饰（V5.0 §10.4 第 4 条）：久别重逢，先回忆旧事"""
    v = list(v)
    if idle_seconds is not None and idle_seconds > 24 * 3600:
        v[D] *= 1.15
    return v


def normalize(v, a_min=None):
    """钳位 + 归一（V5.0 §10.4 收尾两步）"""
    v = [max(0.0, float(x)) for x in v]
    v[A] = max(v[A], A_MIN if a_min is None else a_min)
    s = sum(v)
    if s <= 0:
        return list(SCENE_WEIGHTS["普通对话"])
    return [x / s for x in v]


def allocate(budget, v, cell_min=None):
    """权重 → 四格 token 配额（V5.0 §10.5 + 保底）

    返回 dict: identity / longterm / recent / recall
    保证四格合计不超过 budget，且每格 >= CELL_MIN（预算够的话）。
    """
    cmin = CELL_MIN if cell_min is None else max(0, int(cell_min))
    raw = {
        "identity": (v[A] + v[B]) * budget,
        "longterm": v[D] * budget,
        "recent": v[C] * budget,
        "recall": (v[E] + v[F]) * budget,
    }
    keys = list(raw.keys())
    # 1) 先保底
    out = {}
    for k in keys:
        out[k] = max(cmin, int(round(raw[k])))
    total = sum(out.values())
    # 2) 超了就从「非保底部分」按比例削
    if total > budget:
        excess = total - budget
        flex = {k: max(0, out[k] - cmin) for k in keys}
        flex_sum = sum(flex.values())
        if flex_sum > 0:
            for k in keys:
                out[k] -= int(round(excess * flex[k] / float(flex_sum)))
    # 3) 还超（保底本身就超）就硬削，从最大的那格削
    total = sum(out.values())
    while total > budget:
        k = max(keys, key=lambda x: out[x])
        if out[k] <= 1:
            break
        out[k] -= 1
        total -= 1
    # 4) 没花完的补给「最近聊的」（真人说话最依赖眼前）
    total = sum(out.values())
    if total < budget:
        out["recent"] += budget - total
    return out


def compute(query, budget, hormones=None, turns=None, idle_seconds=None,
            recent_lines=None, db=None):
    """一步到位：算场景 → 套修饰 → 归一 → 分配

    db 传进来时会读卡里的 runtime_config（支持热改，见 §10.9）。
    不传就用代码里的默认表 —— 离线测试 / 没库的场景照旧能跑。

    返回 dict：
      scene     —— 判出来的场景名
      weights   —— 归一后的六维权重
      budget    —— 四格配额
      trace     —— 人话解释（为什么这么分）
      source    —— "db"（卡里的配置）还是 "default"
    """
    cfg = load_config(db) if db is not None else _DEFAULT_CFG
    scene = classify_scene(query, recent_lines)
    v0 = base_weights(scene, cfg)
    v = apply_hormones(v0, hormones)
    v = apply_intent(v, query)
    v = apply_session(v, turns)
    v = apply_idle(v, idle_seconds)
    v = normalize(v, cfg.get("a_min"))
    b = allocate(budget, v, cfg.get("cell_min"))
    trace = "场景「%s」→ 格1 %d / 格2 %d / 格3 %d / 格4 %d" % (
        scene, b["identity"], b["longterm"], b["recent"], b["recall"])
    return {"scene": scene, "weights": v, "budget": b, "trace": trace,
            "source": "db" if db is not None else "default"}


def weights_line(v):
    """给人看的一行权重"""
    return " ".join("%s=%.2f" % (n, x) for n, x in zip(DIM_NAMES, v))
