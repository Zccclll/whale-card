# -*- coding: utf-8 -*-
"""T4-4 突触联想 + 电势扩散

为什么要有这一层：
    recall() 解决的是「直接想起来」——问什么像什么就翻什么。
    但人的记忆不是这样工作的：说到「烧烤」会顺带想起「那天很开心」，
    说到「学校门口」会想起「同桌」。这是**联想**，不是检索。

    检索是「找」，联想是「顺着找」。前者靠相似度，后者靠连线。

做法（V5.0 §9.6）：
    1. 建突触：两条记忆之间连一条带类型、带权重的边
         co        共激活  1.00   同一次查询被一起想起来
         temporal  时序    1.20   同一段对话里挨着发生
         tag       同标签  1.15   同一分区 + 共享实体
         ground    同地点  1.25   同一个地方
    2. 扩散：从直接命中的种子出发，电势沿突触往外传
         pot_next = pot_cur * LAYER_DECAY * synapse_weight
         LAYER_DECAY = 0.75   （V3.4 是 0.5，第三层直接死）
         POT_MIN_ACTIVATE = 0.05
         D_MAX = 3            （最多跳三跳）
    3. 被激活的邻居以「联想加成」的形式并入召回结果，
       而不是替换掉直接命中 —— 直接命中永远排前面。

注意：这一层是**加法**，不是替代。关掉它 recall 照样能用。
"""
import json
import math
import time

DAY = 86400.0

# 扩散参数（V5.0 §11 超参表）
LAYER_DECAY = 0.75
POT_MIN_ACTIVATE = 0.05
D_MAX = 3

# 关系边类型与基础权重（V5.0 §9.6）
EDGE_TYPES = {
    "co": 1.00,        # 共激活
    "temporal": 1.20,  # 时序
    "tag": 1.15,       # 同标签
    "ground": 1.25,    # 同地点
}

# 建边阈值
TEMPORAL_GAP = 600.0        # 10 分钟内算同一段对话
TEMPORAL_MAX = 6            # 一条最多连 6 个邻居，避免稠密
TAG_MIN_SHARED = 2          # 至少共享 2 个实体才算同标签
MAX_EDGES_PER_NODE = 24     # 每个节点最多 24 条边（防稠密图）
REBUILD_GROWTH = 0.05       # 节点数涨 5% 就重建

# 联想加成上限：再强的联想也不能盖过直接命中
ASSOC_BONUS_MAX = 0.55
# 联想结果的最低分数（相对种子最高分）
ASSOC_KEEP_RATIO = 0.25


def _norm_entities(raw):
    """mem.entities 存的是个数，不是集合。这里用 detail 里的实体做替代。"""
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        n = 0
    return n


_PREPS = ("在", "去", "到", "从", "往", "于")


def _strip_prep(s):
    """去掉地点前的介词：「在学校门口」→「学校门口」"""
    t = (s or "").strip()
    while t and t[0] in _PREPS:
        t = t[1:]
    return t


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class SynapseNet(object):
    """突触网络：建边 + 电势扩散

    用一张独立的表存边，不污染 mem 表。
    """

    def __init__(self, db):
        self.db = db
        self._adj = None          # {mem_id: [(other_id, kind, weight)]}
        self._n_at_build = 0

    # ---------- 建表 ----------
    def ensure_schema(self):
        c = self.db.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS synapse (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            a_id        INTEGER NOT NULL,
            b_id        INTEGER NOT NULL,
            kind        TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 1.0,
            fire_count  INTEGER NOT NULL DEFAULT 0,
            last_fire_ts REAL NOT NULL DEFAULT 0,
            create_ts   REAL NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_syn_pair"
                  " ON synapse(a_id, b_id, kind)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_syn_a ON synapse(a_id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_syn_b ON synapse(b_id)")
        self.db.commit()

    # ---------- 惰性建边 ----------
    def _stale(self):
        """只有「记忆条数明显涨了」才算过期。

        🔧 注意：`_adj is None` 不等于过期 —— 那只是还没载入内存。
           早期版本把这两件事混在一起，导致每次新建 SynapseNet 都会
           全量重建，手工插进去的边被 DELETE 掉。载入 ≠ 重建。
        """
        if self._adj is None:
            return False
        n = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        return n >= self._n_at_build * (1.0 + REBUILD_GROWTH) + 5

    def ensure(self):
        if self._adj is None:
            self.ensure_schema()
            self._load_adj()
            # 库里一条边都没有、但记忆够多 → 说明还没建过，建一次
            if not self._adj:
                n = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
                if n >= 2:
                    return self.rebuild()
            return self._adj
        if self._stale():
            return self.rebuild()
        return self._adj

    def _load_adj(self):
        """从 synapse 表读进内存，不重建"""
        adj = {}
        for s in self.db.execute("SELECT a_id,b_id,kind,weight FROM synapse"):
            a, b, k, w = s["a_id"], s["b_id"], s["kind"], s["weight"]
            adj.setdefault(a, []).append((b, k, w))
            adj.setdefault(b, []).append((a, k, w))
        for mid in list(adj):
            if len(adj[mid]) > MAX_EDGES_PER_NODE:
                adj[mid] = sorted(adj[mid], key=lambda x: -x[2])[
                    :MAX_EDGES_PER_NODE]
        self._adj = adj
        self._n_at_build = self.db.execute(
            "SELECT COUNT(*) FROM mem").fetchone()[0]
        return adj

    def rebuild(self):
        """从 mem / detail / recall_event 重建整张突触网

        每次全量重建（几百到几千条，几十毫秒），比增量维护简单且不会错。
        """
        self.ensure_schema()
        rows = self.db.execute(
            "SELECT id, region, create_ts, entities, pool FROM mem"
        ).fetchall()
        n = len(rows)
        self._n_at_build = n
        if n < 2:
            self._adj = {}
            return self._adj

        # 1) temporal：时间上挨着的两条
        by_time = sorted(rows, key=lambda r: r["create_ts"] or 0)
        temporal_pairs = set()
        for i, r in enumerate(by_time):
            cnt = 0
            for j in range(i + 1, min(i + 1 + TEMPORAL_MAX, n)):
                q = by_time[j]
                gap = (q["create_ts"] or 0) - (r["create_ts"] or 0)
                if gap > TEMPORAL_GAP:
                    break
                if gap >= 0:
                    temporal_pairs.add((r["id"], q["id"]))
                    cnt += 1
                if cnt >= TEMPORAL_MAX:
                    break

        # 2) tag：同分区 + 共享实体（用 detail 的 PLACE/NUMBER 词做代理）
        place_of = {}
        for d in self.db.execute(
                "SELECT parent_id, text FROM detail WHERE kind='PLACE'"):
            place_of.setdefault(d["parent_id"], set()).add(
                (d["text"] or "").strip())
        by_region = {}
        for r in rows:
            by_region.setdefault(r["region"] or "episode", []).append(r)
        tag_pairs = set()
        ground_pairs = set()
        for _reg, lst in by_region.items():
            # 同分区里按实体数分组：实体多的和实体多的连（有内容才值得连）
            rich = [r for r in lst if _norm_entities(r["entities"]) >= 2]
            for i in range(len(rich)):
                a = rich[i]
                for j in range(i + 1, min(i + 1 + 4, len(rich))):
                    b = rich[j]
                    if abs((a["create_ts"] or 0) - (b["create_ts"] or 0)) < 3 * DAY:
                        tag_pairs.add((a["id"], b["id"]))
        # ground：同一个地方
        # 🔧 不能要求地点字符串完全相等 —— 「在学校门口的路边摊」和
        #    「在学校门口」是同一个地方，只是说法长短不同。
        #    改成：一个地点是另一个的子串，或者两者去掉介词后共享 ≥3 字前缀。
        place_keys = []
        for mid, ps in place_of.items():
            for p in ps:
                q = _strip_prep(p)
                if len(q) >= 2:
                    place_keys.append((q, mid))
        by_place = {}
        for q, mid in place_keys:
            by_place.setdefault(q, []).append(mid)
        ground_pairs = set()
        keys = list(by_place)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                short, long_ = (a, b) if len(a) <= len(b) else (b, a)
                if short in long_ or _common_prefix(a, b) >= 3:
                    for ma in by_place[a]:
                        for mb in by_place[b]:
                            if ma != mb:
                                ground_pairs.add((ma, mb))

        # 3) co：同一次查询被一起想起来的（recall_event 同 query_hash）
        co_pairs = set()
        groups = self.db.execute(
            "SELECT query_hash, GROUP_CONCAT(mem_id) AS ids FROM recall_event"
            " WHERE query_hash IS NOT NULL GROUP BY query_hash"
            " HAVING COUNT(*) >= 2 LIMIT 500").fetchall()
        for g in groups:
            ids = [int(x) for x in (g["ids"] or "").split(",") if x]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    co_pairs.add((ids[i], ids[j]))

        # 写库
        now = time.time()
        c = self.db.cursor()
        c.execute("DELETE FROM synapse")
        add = []
        for kind, pairs in (("temporal", temporal_pairs),
                            ("tag", tag_pairs),
                            ("ground", ground_pairs),
                            ("co", co_pairs)):
            w = EDGE_TYPES[kind]
            for a, b in pairs:
                if a == b:
                    continue
                lo, hi = (a, b) if a < b else (b, a)
                add.append((lo, hi, kind, w, now))
        # 每节点边数封顶（按 id 顺序截，简单可复现）
        if add:
            c.executemany(
                "INSERT OR IGNORE INTO synapse(a_id,b_id,kind,weight,create_ts)"
                " VALUES(?,?,?,?,?)", add)
        self.db.commit()

        # 建邻接表（直接复用 _load_adj，逻辑只写一处）
        self._adj = None
        self._load_adj()
        return self._adj

    # ---------- 电势扩散 ----------
    def spread(self, seeds, top_k=5):
        """从种子出发沿突触扩散电势

        seeds: [(mem_id, pot), ...]  pot 已归一化到 0~1
        返回：[(mem_id, pot, path_kind), ...] —— 不含种子本身
        """
        adj = self.ensure()
        if not adj or not seeds:
            return []
        pot = {}
        for mid, p in seeds:
            if p > pot.get(mid, 0.0):
                pot[mid] = p
        seed_ids = set(pot)
        frontier = dict(pot)
        acc = {}
        for _layer in range(D_MAX):
            nxt = {}
            for mid, p in frontier.items():
                for other, kind, w in adj.get(mid, ()):
                    if other in seed_ids:
                        continue          # 不回灌种子，避免自我加强
                    np_ = p * LAYER_DECAY * w
                    if np_ < POT_MIN_ACTIVATE:
                        continue
                    if np_ > nxt.get(other, 0.0):
                        nxt[other] = np_
                        acc[other] = kind
            if not nxt:
                break
            # 同一层内合并（取最大），不累加 —— 累加会让稠密节点爆炸
            for mid, p in nxt.items():
                if p > pot.get(mid, 0.0):
                    pot[mid] = p
            frontier = nxt
        out = [(mid, p, acc.get(mid, "co")) for mid, p in pot.items()
               if mid not in seed_ids]
        out.sort(key=lambda x: -x[1])
        return out[:top_k]

    # ---------- 加固突触（被一起想起的边变粗） ----------
    def reinforce(self, mem_ids, now=None):
        """一次召回里同时出现的记忆，它们之间的边 +1"""
        if len(mem_ids) < 2:
            return 0
        now = now or time.time()
        c = self.db.cursor()
        n = 0
        for i in range(len(mem_ids)):
            for j in range(i + 1, len(mem_ids)):
                a, b = mem_ids[i], mem_ids[j]
                lo, hi = (a, b) if a < b else (b, a)
                c.execute(
                    "UPDATE synapse SET fire_count=fire_count+1, last_fire_ts=?"
                    " WHERE a_id=? AND b_id=?", (now, lo, hi))
                n += c.rowcount
        self.db.commit()
        return n

    def stats(self):
        self.ensure_schema()
        total = self.db.execute("SELECT COUNT(*) FROM synapse").fetchone()[0]
        kinds = {}
        for r in self.db.execute(
                "SELECT kind, COUNT(*) AS n FROM synapse GROUP BY kind"):
            kinds[r["kind"]] = r["n"]
        nodes = self.db.execute(
            "SELECT COUNT(DISTINCT a_id) FROM synapse").fetchone()[0]
        return {"edges": total, "kinds": kinds, "nodes": nodes,
                "avg_degree": round(total * 2.0 / nodes, 2) if nodes else 0.0}
