# -*- coding: utf-8 -*-
"""分片并行下载 bge-small-zh-v1.5 的 model.safetensors。

单连接只有 40~70 KB/s，93.5 MB 要半小时以上，还容易中途断。
改成 8 个分片各自 Range 请求，谁慢谁重试，最后按顺序拼接。
"""
import os
import sys
import time
import threading
import urllib.request

URL = 'https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/model.safetensors'
URL_MIRROR = 'https://hf-mirror.com/BAAI/bge-small-zh-v1.5/resolve/main/model.safetensors'
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'models', 'bge-small-zh-v1.5', 'model.safetensors')
PARTS = 8
CHUNK = 1 << 16
RETRY = 40


def total_size():
    for u in (URL, URL_MIRROR):
        try:
            req = urllib.request.Request(u, method='HEAD')
            r = urllib.request.urlopen(req, timeout=20)
            n = int(r.headers['Content-Length'])
            if n > 1000000:
                return n, u
        except Exception:
            continue
    return None, None


def fetch(url, idx, start, end, part_path, stat):
    """下载 [start, end] 闭区间到 part_path，支持断点续传。"""
    have = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    want = end - start + 1
    if have >= want:
        stat[idx] = want
        return True
    for attempt in range(RETRY):
        try:
            s = start + have
            req = urllib.request.Request(url, headers={'Range': 'bytes=%d-%d' % (s, end)})
            r = urllib.request.urlopen(req, timeout=30)
            with open(part_path, 'ab') as f:
                while have < want:
                    c = r.read(CHUNK)
                    if not c:
                        break
                    f.write(c)
                    have += len(c)
                    stat[idx] = have
            if have >= want:
                return True
        except Exception:
            time.sleep(1.0 + attempt * 0.3)
        have = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        stat[idx] = have
    return have >= want


def main():
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    total, url = total_size()
    if not total:
        print('拿不到文件大小，网络可能又断了')
        return 1
    print('总大小 %.2f MB  源 %s' % (total / 1048576.0, url))
    if os.path.exists(DST) and os.path.getsize(DST) == total:
        print('已经下好了')
        return 0

    step = total // PARTS
    jobs = []
    for i in range(PARTS):
        start = i * step
        end = total - 1 if i == PARTS - 1 else (i + 1) * step - 1
        jobs.append((i, start, end, DST + '.part%d' % i))

    stat = {i: 0 for i in range(PARTS)}
    threads = []
    for i, s, e, p in jobs:
        t = threading.Thread(target=fetch, args=(url, i, s, e, p, stat), daemon=True)
        t.start()
        threads.append(t)

    t0 = time.time()
    while any(t.is_alive() for t in threads):
        time.sleep(5)
        done = sum(stat.values())
        el = max(time.time() - t0, 0.1)
        pct = done * 100.0 / total
        print('  %5.1f%%  %6.1f/%.1f MB  %.0f KB/s' %
              (pct, done / 1048576.0, total / 1048576.0, done / 1024 / el), flush=True)
        if done >= total:
            break

    for t in threads:
        t.join(timeout=5)

    done = sum(stat.values())
    if done < total:
        print('没下完：%d / %d' % (done, total))
        return 2

    # 按顺序拼接
    print('拼接中…')
    with open(DST, 'wb') as out:
        for i, s, e, p in jobs:
            with open(p, 'rb') as f:
                while True:
                    c = f.read(1 << 20)
                    if not c:
                        break
                    out.write(c)
    size = os.path.getsize(DST)
    print('完成 %d 字节' % size)
    if size == total:
        for i, s, e, p in jobs:
            try:
                os.remove(p)
            except OSError:
                pass
        print('✅ %s' % DST)
        return 0
    print('大小不对，保留分片')
    return 3


if __name__ == '__main__':
    sys.exit(main())
