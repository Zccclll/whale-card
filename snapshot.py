# -*- coding: utf-8 -*-
"""T6-3 加载 / 卸载序列 + 快照

为什么需要这个 —— 这张卡的本质是「能拔下来带走的」。拔之前必须保证：
    1. 没有写到一半的东西（WAL 里悬着的事务要 checkpoint 掉）
    2. 拔的过程中断了，下次插上要能自己发现
    3. 想回到某个时间点，要有快照可以退

三条纪律：
  1. **拔卡前必须走卸载序列**，不许直接拔。
  2. **快照必须校验**（sha256 + 行数），坏掉的快照宁可拒绝也不许恢复。
  3. **崩溃要留痕**：启动时写 `clean=0`，正常退出写 `clean=1`；
     下次启动看到 `clean=0` 就知道上次是硬拔的，要提示。

用法：
    python snapshot.py list                    # 列出所有快照
    python snapshot.py make [--note "..."]     # 打一个快照
    python snapshot.py restore <名字>          # 恢复（会先自动备份当前库）
    python snapshot.py check                   # 检查上次是否干净退出
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "card.db")
SNAP_DIR = os.path.join(HERE, "snapshots")
MANIFEST = "manifest.json"


# ──────────────────────────────────────────────────────
# 基础
# ──────────────────────────────────────────────────────
def _sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _db_counts(db_path):
    """读快照里的关键行数，用来核对「这个快照里到底有什么」"""
    out = {}
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
        c.row_factory = sqlite3.Row
        for t in ("mem", "detail", "dialogue", "synapse",
                  "skill", "recall_event", "trace"):
            try:
                out[t] = c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except Exception:
                out[t] = None
        try:
            row = c.execute("SELECT val FROM meta WHERE key='mood_global'").fetchone()
            out["mood"] = row["val"] if row else None
        except Exception:
            out["mood"] = None
        c.close()
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def _checkpoint(db_path):
    """把 WAL 里的东西全部落进主库，然后截断 WAL

    这是卸载序列里最关键的一步：不做的话，card.db 看起来是完整的，
    但最新那几百条其实还在 card.db-wal 里 —— 只拷 card.db 就丢了。
    """
    c = sqlite3.connect(db_path)
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.commit()
    finally:
        c.close()


# ──────────────────────────────────────────────────────
# 快照
# ──────────────────────────────────────────────────────
def make(db_path=DEFAULT_DB, note="", snap_dir=SNAP_DIR, keep=10):
    """打一个快照。

    用 SQLite 的 backup API 而不是复制文件 —— 复制文件在有连接时可能拷到
    写到一半的状态；backup API 保证拿到的是一个事务一致的库。
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError("找不到卡：%s" % db_path)
    os.makedirs(snap_dir, exist_ok=True)

    _checkpoint(db_path)

    ts = time.time()
    name = time.strftime("snap-%Y%m%d-%H%M%S", time.localtime(ts))
    path = os.path.join(snap_dir, name + ".db")
    n = 1
    while os.path.exists(path):
        path = os.path.join(snap_dir, "%s-%d.db" % (name, n))
        n += 1

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    counts = _db_counts(path)
    info = {
        "name": os.path.splitext(os.path.basename(path))[0],
        "file": os.path.basename(path),
        "ts": ts,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
        "note": note,
        "size": os.path.getsize(path),
        "sha256": _sha256_file(path),
        "counts": counts,
    }

    mpath = os.path.join(snap_dir, MANIFEST)
    all_snaps = []
    if os.path.exists(mpath):
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                all_snaps = json.load(f)
        except Exception:
            all_snaps = []
    all_snaps.append(info)
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(all_snaps, f, ensure_ascii=False, indent=2)

    # 只留最近 keep 个（老的删掉，但 manifest 里的记录一并清）
    all_snaps.sort(key=lambda x: x["ts"])
    while len(all_snaps) > keep:
        old = all_snaps.pop(0)
        old_path = os.path.join(snap_dir, old["file"])
        try:
            os.remove(old_path)
        except Exception:
            pass
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(all_snaps, f, ensure_ascii=False, indent=2)

    return info


def list_snaps(snap_dir=SNAP_DIR):
    mpath = os.path.join(snap_dir, MANIFEST)
    if not os.path.exists(mpath):
        return []
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def restore(name, db_path=DEFAULT_DB, snap_dir=SNAP_DIR, force=False):
    """恢复一个快照。

    恢复前先把当前库备份一份（`card.db.bak-<时间>-pre-restore`），
    这样「恢复错了」还能退回去 —— 恢复是不可逆操作里最危险的一种。
    """
    snaps = list_snaps(snap_dir)
    hit = None
    for s in snaps:
        if s["name"] == name or s["file"] == name or s["name"] == os.path.splitext(name)[0]:
            hit = s
            break
    if hit is None:
        raise FileNotFoundError("没有这个快照：%s（用 list 看有哪些）" % name)

    src = os.path.join(snap_dir, hit["file"])
    if not os.path.exists(src):
        raise FileNotFoundError("快照文件不见了：%s" % src)

    # 校验 —— 坏掉的快照宁可拒绝也不许恢复
    actual = _sha256_file(src)
    if actual != hit["sha256"]:
        raise ValueError("快照已损坏，拒绝恢复\n  期望 %s\n  实际 %s"
                         % (hit["sha256"][:16], actual[:16]))

    backup = None
    if os.path.exists(db_path):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = "%s.bak-%s-pre-restore" % (db_path, stamp)
        shutil.copy2(db_path, backup)

    # 清掉 WAL/SHM，否则恢复后旧 WAL 会「盖回去」
    for suf in ("-wal", "-shm"):
        p = db_path + suf
        if os.path.exists(p):
            os.remove(p)

    shutil.copy2(src, db_path)
    return {"restored": hit["name"], "backup": backup, "counts": hit["counts"]}


# ──────────────────────────────────────────────────────
# 加载 / 卸载序列
# ──────────────────────────────────────────────────────
def load_sequence(db_path=DEFAULT_DB):
    """插卡：7 步。任何一步失败都要明确说出来，不许假装成功。"""
    steps = []

    def step(ok, msg):
        steps.append({"ok": bool(ok), "msg": msg})
        return ok

    # 0. 冷副本守卫 —— 备份目录里的 card.db 是「尸体」，不许挂载
    #    为什么：每日备份就在复制 card.db。复制出来的两份各自演化，就是两个她。
    #    V3.7 铁律：一旦复制数据库就会分裂。所以这里必须拦。
    cold_marker = os.path.join(os.path.dirname(os.path.abspath(db_path)), ".cold")
    if os.path.exists(cold_marker):
        try:
            with open(cold_marker, "r", encoding="utf-8") as f:
                why = f.read().strip()
        except Exception:
            why = ""
        return {"ok": False, "steps": [{
            "ok": False,
            "msg": "拒绝加载：这是备份里的冷副本（看到 .cold 标记）%s。"
                   "冷副本只能看不能跑 —— 跑起来它就会变成第二个她。" % (
                       ("：" + why) if why else "")}]}

    # 1. 卡在不在
    if not step(os.path.exists(db_path), "卡文件存在：%s" % os.path.abspath(db_path)):
        return {"ok": False, "steps": steps}

    # 2. 上次是不是干净退出
    dirty = _read_flag(db_path)
    step(True, "上次退出：%s" % ("干净" if dirty.get("clean") else
                                 "⚠ 非正常（可能硬拔/崩溃）"))

    # 3. 库能不能打开、结构对不对
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        need = {"mem", "detail", "meta"}
        step(need <= tables, "库结构完整（%d 张表，缺 %s）"
             % (len(tables), sorted(need - tables) or "无"))
    except Exception as e:
        step(False, "库打不开：%s" % e)
        return {"ok": False, "steps": steps}

    # 4. 完整性检查（快速）
    try:
        c = sqlite3.connect(db_path)
        r = c.execute("PRAGMA quick_check").fetchone()[0]
        c.close()
        step(r == "ok", "完整性检查：%s" % r)
    except Exception as e:
        step(False, "完整性检查失败：%s" % e)
        return {"ok": False, "steps": steps}

    # 5. 标记「正在使用」—— 崩溃时下次就知道
    _write_flag(db_path, {"clean": 0, "since": time.time()})

    # 6. 载入卡（这一步会跑 migrate / 建索引）
    try:
        sys.path.insert(0, HERE)
        from card2 import MemoryCard
        card = MemoryCard(db_path)
        st = card.stats()
        card.close()
        step(True, "卡已载入：%d 条记忆 / %d 条细节"
             % (st["total"], st["details"]))
    except Exception as e:
        step(False, "载入失败：%s: %s" % (type(e).__name__, e))
        return {"ok": False, "steps": steps}

    # 7. 身份锚
    try:
        from identity import IdentityAnchor
        c2 = sqlite3.connect(db_path)
        ia = IdentityAnchor(c2, card_dir=os.path.dirname(os.path.abspath(db_path)))
        sealed = ia.is_sealed()
        c2.close()
        step(True, "身份锚：%s" % ("已盖章" if sealed else "未盖章（首次使用需 seal）"))
    except Exception as e:
        step(False, "身份锚检查失败：%s" % e)

    return {"ok": all(s["ok"] for s in steps), "steps": steps}


def unload_sequence(db_path=DEFAULT_DB, snap=True):
    """拔卡：7 步。最后一步才允许物理拔。"""
    steps = []

    def step(ok, msg):
        steps.append({"ok": bool(ok), "msg": msg})
        return ok

    if not step(os.path.exists(db_path), "卡文件存在"):
        return {"ok": False, "steps": steps}

    # 1. WAL checkpoint —— 不做的后果是「看着拔了，最新几百条还在 wal 里」
    try:
        _checkpoint(db_path)
        step(True, "WAL 已落盘并截断")
    except Exception as e:
        step(False, "WAL checkpoint 失败：%s" % e)

    # 2. 完整性
    try:
        c = sqlite3.connect(db_path)
        r = c.execute("PRAGMA quick_check").fetchone()[0]
        c.close()
        step(r == "ok", "完整性检查：%s" % r)
    except Exception as e:
        step(False, "完整性检查失败：%s" % e)

    # 3. 落一个快照（可选但强烈建议）
    if snap:
        try:
            info = make(db_path, note="拔卡前自动快照")
            step(True, "已打快照：%s（%d 条记忆）"
                 % (info["name"], info["counts"].get("mem") or 0))
        except Exception as e:
            step(False, "打快照失败：%s" % e)

    # 4. 再 checkpoint 一次（快照可能带起新 WAL）
    try:
        _checkpoint(db_path)
        step(True, "二次落盘完成")
    except Exception as e:
        step(False, "二次落盘失败：%s" % e)

    # 5. 清掉 -wal / -shm
    ok_clean = True
    for suf in ("-wal", "-shm"):
        p = db_path + suf
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                ok_clean = False
    step(ok_clean, "残留 WAL/SHM 已清理")

    # 6. 标记干净退出
    _write_flag(db_path, {"clean": 1, "since": time.time()})
    step(True, "已标记干净退出")

    # 7. 最后确认没有别的进程占着它
    locked = _is_locked(db_path)
    step(not locked, "无其它进程占用" if not locked else "⚠ 还有进程占用（别拔）")

    return {"ok": all(s["ok"] for s in steps), "steps": steps}


# ──────────────────────────────────────────────────────
# 退出标记 / 占用检测
# ──────────────────────────────────────────────────────
def _flag_path(db_path):
    return db_path + ".state.json"


def _read_flag(db_path):
    p = _flag_path(db_path)
    if not os.path.exists(p):
        return {"clean": 1}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"clean": 0}


def _write_flag(db_path, obj):
    try:
        with open(_flag_path(db_path), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass


def _is_locked(db_path):
    """能不能拿到写锁 —— 拿不到说明有别的进程在用"""
    try:
        c = sqlite3.connect(db_path, timeout=0.5)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.rollback()
            return False
        finally:
            c.close()
    except Exception:
        return True


def check(db_path=DEFAULT_DB):
    """检查上次是不是干净退出"""
    f = _read_flag(db_path)
    return {
        "clean": bool(f.get("clean")),
        "since": f.get("since"),
        "since_str": (time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(f["since"]))
                      if f.get("since") else None),
        "locked": _is_locked(db_path),
    }


# ──────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="数字生命卡 · 快照与加载/卸载")
    ap.add_argument("cmd", choices=["make", "list", "restore", "check",
                                    "load", "unload", "counts"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--note", default="")
    ap.add_argument("--keep", type=int, default=10)
    # 收工脚本 stop.ps1 会传这个：快照多到一定数量就没必要每次拔卡都打一张，
    # 但它仍然是「可选而非常规」—— 默认还是要打，快照是最便宜的后悔药。
    ap.add_argument("--no-snap", action="store_true",
                    help="unload 时不打快照（只做 checkpoint）")
    a = ap.parse_args(argv)

    if a.cmd == "make":
        info = make(a.db, a.note, keep=a.keep)
        print("✅ 快照 %s  %s" % (info["name"], info["time"]))
        print("   %d 字节  sha256=%s…" % (info["size"], info["sha256"][:16]))
        print("   %s" % info["counts"])
    elif a.cmd == "list":
        snaps = list_snaps()
        if not snaps:
            print("（还没有快照）")
        for s in snaps:
            print("%-22s %s  %8.1f KB  mem=%-5s  %s"
                  % (s["name"], s["time"], s["size"] / 1024.0,
                     (s["counts"] or {}).get("mem"), s.get("note") or ""))
    elif a.cmd == "restore":
        if not a.arg:
            print("要恢复哪个？先跑 list", file=sys.stderr)
            return 2
        r = restore(a.arg, a.db)
        print("✅ 已恢复到 %s" % r["restored"])
        if r["backup"]:
            print("   恢复前旧库备份在：%s" % r["backup"])
        print("   %s" % r["counts"])
    elif a.cmd == "check":
        r = check(a.db)
        print("上次退出：%s" % ("干净" if r["clean"] else "⚠ 非正常"))
        print("标记时间：%s" % (r["since_str"] or "无"))
        print("当前占用：%s" % ("有进程在用" if r["locked"] else "空闲"))
    elif a.cmd == "load":
        r = load_sequence(a.db)
        for s in r["steps"]:
            print("  %s %s" % ("✅" if s["ok"] else "❌", s["msg"]))
        print("\n插卡：%s" % ("完成 ✅" if r["ok"] else "失败 ❌"))
        return 0 if r["ok"] else 1
    elif a.cmd == "unload":
        r = unload_sequence(a.db, snap=not a.no_snap)
        for s in r["steps"]:
            print("  %s %s" % ("✅" if s["ok"] else "❌", s["msg"]))
        print("\n拔卡：%s" % ("可以拔了 ✅" if r["ok"] else "先别拔 ❌"))
        return 0 if r["ok"] else 1
    elif a.cmd == "counts":
        print(_db_counts(a.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
