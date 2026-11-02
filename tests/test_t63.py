# -*- coding: utf-8 -*-
"""T6-3 验收：快照 + 加载/卸载序列 + 崩溃检测"""
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import snapshot                                      # noqa: E402
from card2 import MemoryCard                         # noqa: E402

ok_n = 0
bad_n = 0


def ck(name, cond, extra=""):
    global ok_n, bad_n
    if cond:
        ok_n += 1
        print("  ✅ %s %s" % (name, extra))
    else:
        bad_n += 1
        print("  ❌ %s %s" % (name, extra))


TMPDIR = os.path.join(HERE, "_t63")
TMP = os.path.join(TMPDIR, "card.db")
SNAPDIR = os.path.join(TMPDIR, "snapshots")

if os.path.isdir(TMPDIR):
    shutil.rmtree(TMPDIR, ignore_errors=True)
os.makedirs(TMPDIR, exist_ok=True)


# 造记忆时每句必须长得不一样 —— 去重（Jaccard 0.72）会把
# 「这是第1条测试记忆…」「这是第2条测试记忆…」判成同一句，压成一条。
FILLER = [
    "今天中午在学校食堂吃了白米饭配红烧肉，很满足",
    "数学老师讲导数讲到第三遍我才听懂，脑子有点晕",
    "放学路上看见一只橘猫蹲在墙角，我蹲下来看了它两分钟",
    "同桌说他以后想学临床医学，我说我想搞生物科研",
    "晚上回家把硬盘的型号又比了一遍，还是没下单",
    "物理周测考砸了，最后一题完全没思路，心里挺难受",
    "妈妈煮了玉米排骨汤，我喝了两碗，肚子都撑圆了",
    "半夜躺着想数字生命卡这件事，越想越清醒睡不着",
    "早读背英语单词背到 abandon 就想笑，这词太应景了",
    "体育课跑八百米，跑完在操场边坐了好久才缓过来",
    "把窗户打开透气，风一吹课本翻页，我干脆不看了",
    "班主任在班会上说高考只剩两百多天，全班安静了三秒",
    "食堂新出了南瓜饼，排队的人从窗口一直绕到门口",
    "我把手机里那首歌单曲循环了十几遍，越听越上头",
    "下雨没带伞，蹭了同学的伞回家，一路都在踩水坑",
    "整理错题本整理到凌晨一点，写完那一页突然不困了",
    "爸问我最近学习怎么样，我说还行，其实心里没底",
    "在阳台看了一会儿月亮，今天的月亮有点偏黄",
    "买了瓶冰镇可乐，第一口下去整个人都活过来了",
    "跟朋友在群里聊到半夜，聊的全是没用的废话",
    "早上差点睡过头，袜子都来不及配对就冲出门了",
    "把那本讲记忆的书翻到一半，越看越觉得有意思",
    "晚饭后陪奶奶在小区里走了两圈，她一直在讲以前的事",
    "坐在窗边写作业，外面有只鸟叫了一下午，烦得很",
]


def fresh(n=5):
    """造一张新卡，n 条记忆"""
    for suf in ("", "-wal", "-shm", ".state.json"):
        p = TMP + suf
        if os.path.exists(p):
            for _ in range(20):            # Windows 上文件可能还被上一个连接占着
                try:
                    os.remove(p)
                    break
                except PermissionError:
                    time.sleep(0.1)
    c = MemoryCard(TMP)
    for i in range(n):
        c.remember(FILLER[i % len(FILLER)], ts=time.time() - 86400 * (n - i))
    st = c.stats()
    c.close()
    return st


# ══════════════════════════════════════════════════════
print("=" * 50)
print("【1】打快照")
print("=" * 50)
st0 = fresh(5)
ck("造了 5 条", st0["total"] == 5, "total=%d" % st0["total"])

info = snapshot.make(TMP, "测试快照", snap_dir=SNAPDIR, keep=10)
ck("返回快照名", bool(info.get("name")), info["name"])
ck("快照文件存在", os.path.exists(os.path.join(SNAPDIR, info["file"])))
ck("有 sha256", len(info.get("sha256") or "") == 64)
ck("记了行数", (info.get("counts") or {}).get("mem") == 5,
   "mem=%s" % (info.get("counts") or {}).get("mem"))
ck("记了时间", bool(info.get("time")), info["time"])

print()
print("【2】再存几条，然后恢复快照能退回去")
c = MemoryCard(TMP)
for i in range(5, 12):
    c.remember(FILLER[i % len(FILLER)], ts=time.time() - 3600 * (12 - i))
now_total = c.stats()["total"]
c.close()
ck("现在 12 条", now_total == 12, "total=%d" % now_total)

r = snapshot.restore(info["name"], TMP, snap_dir=SNAPDIR)
ck("恢复成功", r["restored"] == info["name"])
ck("恢复前自动备份了旧库", bool(r.get("backup")) and os.path.exists(r["backup"]),
   os.path.basename(r.get("backup") or ""))

c = MemoryCard(TMP)
after = c.stats()["total"]
c.close()
ck("恢复后回到 5 条", after == 5, "total=%d" % after)

print()
print("【3】坏掉的快照要拒绝恢复")
bad_src = os.path.join(SNAPDIR, info["file"])
with open(bad_src, "r+b") as f:
    f.seek(2048)
    f.write(b"\xde\xad\xbe\xef" * 64)          # 故意弄坏中间一段
try:
    snapshot.restore(info["name"], TMP, snap_dir=SNAPDIR)
    ck("损坏快照被拒绝", False)
except ValueError as e:
    ck("损坏快照被拒绝", "损坏" in str(e), str(e)[:40] + "…")

# 不存在的快照
try:
    snapshot.restore("根本没有这个", TMP, snap_dir=SNAPDIR)
    ck("不存在的快照被拒绝", False)
except FileNotFoundError as e:
    ck("不存在的快照被拒绝", True, str(e)[:30] + "…")

print()
print("【4】keep 上限：只留最近 N 个")
snapshot.make(TMP, "第二个", snap_dir=SNAPDIR, keep=3)
snapshot.make(TMP, "第三个", snap_dir=SNAPDIR, keep=3)
snapshot.make(TMP, "第四个", snap_dir=SNAPDIR, keep=3)
snapshot.make(TMP, "第五个", snap_dir=SNAPDIR, keep=3)
snaps = snapshot.list_snaps(SNAPDIR)
ck("只留 3 个", len(snaps) == 3, "有 %d 个" % len(snaps))
files = [s["file"] for s in snaps]
disk = [f for f in os.listdir(SNAPDIR) if f.endswith(".db")]
ck("磁盘上的文件也只剩 3 个", len(disk) == 3, "%s" % disk)
ck("manifest 和磁盘对得上", set(files) == set(disk))

print()
print("【5】加载序列")
st1 = fresh(3)
res = snapshot.load_sequence(TMP)
ck("加载成功", res["ok"], "")
for s in res["steps"]:
    if not s["ok"]:
        print("     失败步骤：%s" % s["msg"])
ck("步骤数 ≥ 6", len(res["steps"]) >= 6, "%d 步" % len(res["steps"]))
ck("加载后标记为「正在使用」", snapshot.check(TMP)["clean"] is False)

print()
print("【6】崩溃检测：写了一半就死")
# 模拟：标记 clean=0，然后「进程没了」
snapshot._write_flag(TMP, {"clean": 0, "since": time.time()})
ck("check 报告非正常退出", snapshot.check(TMP)["clean"] is False)

res2 = snapshot.load_sequence(TMP)
ck("重新插卡能起来", res2["ok"])
msg = " ".join(s["msg"] for s in res2["steps"])
ck("明确提示上次非正常", "非正常" in msg or "硬拔" in msg, msg[:60] + "…")

print()
print("【7】卸载序列")
res3 = snapshot.unload_sequence(TMP, snap=False)
ck("卸载成功", res3["ok"], "")
for s in res3["steps"]:
    if not s["ok"]:
        print("     失败步骤：%s" % s["msg"])
ck("卸载后标记干净", snapshot.check(TMP)["clean"] is True)
ck("没有残留 WAL", not os.path.exists(TMP + "-wal"))
ck("没有残留 SHM", not os.path.exists(TMP + "-shm"))
ck("库还能打开", os.path.exists(TMP))
c = MemoryCard(TMP)
ck("卸载后数据还在", c.stats()["total"] == 3, "total=%d" % c.stats()["total"])
c.close()

print()
print("【8】WAL 里悬着的东西不能丢（卸载最关键的一步）")
fresh(3)
c = MemoryCard(TMP)
c.db.execute("PRAGMA journal_mode=WAL")
for i in range(20, 40):
    c.remember(FILLER[i % len(FILLER)] + "（第%d遍）" % i)
# 注意：这里**故意不 close**，模拟「写了很多但还没落盘」
wal = TMP + "-wal"
ck("此时 WAL 文件存在", os.path.exists(wal),
   "%.1f KB" % (os.path.getsize(wal) / 1024.0 if os.path.exists(wal) else 0))

snapshot._checkpoint(TMP)              # 卸载序列的第 1 步
ck("checkpoint 后 WAL 被截断",
   (not os.path.exists(wal)) or os.path.getsize(wal) == 0,
   "%.1f KB" % (os.path.getsize(wal) / 1024.0 if os.path.exists(wal) else 0))
c.close()

# 只拷 card.db（不拷 wal）也应该是完整的
_c = MemoryCard(TMP)
n_before = _c.stats()["total"]
_c.close()
only_db = os.path.join(TMPDIR, "copied.db")
shutil.copy2(TMP, only_db)
cc = MemoryCard(only_db)
n = cc.stats()["total"]
cc.close()
ck("只拷主库也拿到全部记忆", n == n_before,
   "主库 %d 条 / 拷贝 %d 条" % (n_before, n))

print()
print("【9】占用检测")
c = MemoryCard(TMP)
c.db.execute("BEGIN IMMEDIATE")        # 故意占着写锁
try:
    c.db.execute("INSERT INTO meta(key,val) VALUES('_t63_lock','1')")
    locked = snapshot._is_locked(TMP)
    ck("别的进程占着时能检测到", locked is True)
finally:
    c.db.rollback()
    c.close()
ck("放开后不再报占用", snapshot._is_locked(TMP) is False)

print()
print("【10】快照恢复后系统还能正常工作")
fresh(4)
info2 = snapshot.make(TMP, "恢复测试", snap_dir=SNAPDIR)
c = MemoryCard(TMP)
c.remember("恢复之后新说的一句话，关于明天下午放假要不要出去走走")
c.close()
snapshot.restore(info2["name"], TMP, snap_dir=SNAPDIR)
c = MemoryCard(TMP)
ck("恢复到 4 条", c.stats()["total"] == 4, "total=%d" % c.stats()["total"])
ctx = c.assemble_context("白米饭")
ck("恢复后还能组装上下文", bool(ctx.get("text")))
c.remember("恢复后新写一条，说晚上想早点睡别再熬了")
ck("恢复后还能写", c.stats()["total"] == 5, "total=%d" % c.stats()["total"])
c.close()

# ── 收尾 ──
shutil.rmtree(TMPDIR, ignore_errors=True)

print()
print("=" * 50)
print("T6-3 验收：通过 %d 项，失败 %d 项" % (ok_n, bad_n))
sys.exit(0 if bad_n == 0 else 1)
