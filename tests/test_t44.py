# -*- coding: utf-8 -*-
"""T4-4 验收：突触联想 + 电势扩散

验的是「顺着连线想起来」这件事，不是「检索准不准」。
所以用假数据造一个可控的图，而不是靠真实库碰运气。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synapse as S
from card2 import MemoryCard, POOL_LONG, DAY

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ✅ %s %s" % (name, extra))
    else:
        fail += 1
        print("  ❌ %s %s" % (name, extra))


def say(s):
    print(s)
    sys.stdout.flush()


DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_t44.db")


def fresh():
    for p in (DB, DB + "-wal", DB + "-shm"):
        try:
            os.remove(p)
        except OSError:
            pass
    return MemoryCard(DB)


# ============================================================
say("=== 1. 建表与基础结构 ===")
c = fresh()
net = c.synapse
net.ensure_schema()
cols = {r["name"] for r in c.db.execute("PRAGMA table_info(synapse)")}
check("synapse 有 a_id/b_id", "a_id" in cols and "b_id" in cols)
check("synapse 有 kind（边类型）", "kind" in cols)
check("synapse 有 weight", "weight" in cols)
check("synapse 有 fire_count（被一起想起几次）", "fire_count" in cols)
c.close()

# ============================================================
say("\n=== 2. 电势扩散：三跳衰减 ===")
c = fresh()
net = c.synapse
net.ensure_schema()
now = time.time()
# 手工造一条链 1-2-3-4-5（temporal 权重 1.20）
c.db.execute("DELETE FROM synapse")
rows = [(1, 2), (2, 3), (3, 4), (4, 5)]
c.db.executemany(
    "INSERT INTO synapse(a_id,b_id,kind,weight,create_ts) VALUES(?,?,?,?,?)",
    [(min(a, b), max(a, b), "temporal", S.EDGE_TYPES["temporal"], now)
     for a, b in rows])
c.db.commit()
net._adj = None
adj = net.ensure()
check("邻接表建起来了", len(adj) == 5, "(%d 个节点)" % len(adj))
check("1 号只连 2 号", [x[0] for x in adj[1]] == [2])

spread = net.spread([(1, 1.0)], top_k=10)
d = {mid: (p, k) for mid, p, k in spread}
say("  从 1 号出发：%s" % {k: round(v[0], 3) for k, v in sorted(d.items())})
check("能扩散到 2 号", 2 in d)
check("能扩散到 3 号（两跳）", 3 in d)
check("能扩散到 4 号（三跳）", 4 in d)
check("扩散不到 5 号（超过 D_MAX=3）", 5 not in d)
# 每跳衰减 0.75 * 1.20 = 0.90
check("2 号电势 ≈ 0.90", abs(d[2][0] - 0.90) < 0.01, "(%.3f)" % d[2][0])
check("3 号电势 ≈ 0.81", abs(d[3][0] - 0.81) < 0.01, "(%.3f)" % d[3][0])
check("电势单调下降", d[2][0] > d[3][0] > d[4][0])
check("种子不回灌自己", 1 not in d)
c.close()

# ============================================================
say("\n=== 3. 四种边类型权重不同 ===")
check("co = 1.00", S.EDGE_TYPES["co"] == 1.00)
check("temporal = 1.20", S.EDGE_TYPES["temporal"] == 1.20)
check("tag = 1.15", S.EDGE_TYPES["tag"] == 1.15)
check("ground = 1.25", S.EDGE_TYPES["ground"] == 1.25)
check("ground > temporal > tag > co",
      S.EDGE_TYPES["ground"] > S.EDGE_TYPES["temporal"]
      > S.EDGE_TYPES["tag"] > S.EDGE_TYPES["co"])
check("layer_decay = 0.75", S.LAYER_DECAY == 0.75)
check("pot_min_activate = 0.05", S.POT_MIN_ACTIVATE == 0.05)
check("D_max = 3", S.D_MAX == 3)

# ============================================================
say("\n=== 4. 从真实记忆建边 ===")
c = fresh()
# 🔧 时间必须挨得很近（10 分钟内）才会连出 temporal 边，
#    同时「学校门口」出现两次才连得出 ground 边。
base = now - 1800
for i, (txt, _region) in enumerate([
        ("10月24日在学校门口的路边摊吃烧烤，很开心", "episode"),
        ("那天还拍了视频", "episode"),
        ("我在学校门口等同桌", "episode"),
        ("我喜欢吃白米饭", "preference"),
        ("我主号QQ号是100000000", "fact"),
        ("我暑假靠拍婚礼挣钱", "fact"),
        ("以后想学生物", "self")]):
    c.remember(txt, salience=0.8, ts=base + i * 60, pool=POOL_LONG)
net = c.synapse
adj = net.ensure()
st = net.stats()
say("  建出 %d 条边，类型分布 %s" % (st["edges"], st["kinds"]))
check("建出了边", st["edges"] > 0, "(%d 条)" % st["edges"])
check("有时间边（挨着说的）", st["kinds"].get("temporal", 0) > 0)
check("有地点边（同一个地方）", st["kinds"].get("ground", 0) > 0)
check("每个节点最多 24 条边",
      all(len(v) <= S.MAX_EDGES_PER_NODE for v in adj.values()))

# ============================================================
say("\n=== 5. recall 会带出联想记忆，但排在直接命中之后 ===")
hits_on = c.recall("烧烤", top_k=2, access=False, assoc=True)
hits_off = c.recall("烧烤", top_k=2, access=False, assoc=False)
say("  直接命中（assoc=False）：%s"
    % [(round(s, 3), r["core"][:14]) for s, _w, r in hits_off])
say("  带联想（assoc=True）  ：%s"
    % [(round(s, 3), r["core"][:14]) for s, _w, r in hits_on])
check("关掉联想时结果干净", len(hits_off) <= 2)
check("打开联想后结果变多或持平", len(hits_on) >= len(hits_off))
if hits_on and hits_off:
    check("第一条仍然是直接命中的那条",
          hits_on[0][2]["id"] == hits_off[0][2]["id"])
    check("第一条的分数没被联想改变",
          abs(hits_on[0][0] - hits_off[0][0]) < 1e-6)
    check("联想条目分数低于直接命中", hits_on[-1][0] < hits_on[0][0])

# ============================================================
say("\n=== 6. 联想加成有上限（不能盖过直接命中）===")
check("ASSOC_BONUS_MAX = 0.55", S.ASSOC_BONUS_MAX == 0.55)
check("ASSOC_KEEP_RATIO = 0.25", S.ASSOC_KEEP_RATIO == 0.25)
if len(hits_on) > len(hits_off):
    top = hits_on[0][0]
    check("联想分数不超过直接命中的 55%%", hits_on[-1][0] <= top * 0.56,
          "(%.3f <= %.3f)" % (hits_on[-1][0], top * 0.56))

# ============================================================
say("\n=== 7. 加固：一起被想起的边变粗 ===")
pairs = [(1, 2), (2, 3)]
c.db.execute("DELETE FROM synapse")
c.db.executemany(
    "INSERT INTO synapse(a_id,b_id,kind,weight,create_ts) VALUES(?,?,?,?,?)",
    [(min(a, b), max(a, b), "co", 1.0, now) for a, b in pairs])
c.db.commit()
n = net.reinforce([1, 2, 3])
check("加固返回被加粗的边数", n >= 1, "(%d)" % n)
fc = c.db.execute(
    "SELECT fire_count FROM synapse WHERE a_id=1 AND b_id=2").fetchone()[0]
check("1-2 那条边的 fire_count 变成 1", fc == 1, "(fire_count=%d)" % fc)

# ============================================================
say("\n=== 8. 幂等 / 重建 ===")
net._adj = None
a1 = len(net.ensure())
net._adj = None
a2 = len(net.ensure())
check("重建两次节点数一致", a1 == a2, "(%d / %d)" % (a1, a2))
e1 = c.db.execute("SELECT COUNT(*) FROM synapse").fetchone()[0]
net._adj = None
net.ensure()
e2 = c.db.execute("SELECT COUNT(*) FROM synapse").fetchone()[0]
check("重建不会让边翻倍", e1 == e2, "(%d / %d)" % (e1, e2))
check("节点数少于 2 时不建边",
      S.SynapseNet(c.db).spread([(999, 1.0)]) == [])

c.close()
for p in (DB, DB + "-wal", DB + "-shm"):
    try:
        os.remove(p)
    except OSError:
        pass

# ============================================================
say("\n" + "=" * 50)
if fail == 0:
    print("T4-4 验收：全部通过 ✅  (%d 项)" % ok)
else:
    print("失败 %d 项 / 通过 %d 项 ❌" % (fail, ok))
sys.exit(1 if fail else 0)
