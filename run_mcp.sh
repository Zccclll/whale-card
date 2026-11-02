#!/bin/bash
# 鱼的家 MCP 启动壳（大肥鱼 fork）：确保 mind_server 在 8848 活着，再 exec whale_mcp.py
# DSH 的 mcp-client 只负责 spawn 这一个脚本，卡的生死它不用管。
cd "$(dirname "$0")" || exit 1
export PYTHONPATH=.
DB="${WHALE_DB:-$PWD/demo/demo_card.db}"
if ! curl -sf -m 2 http://127.0.0.1:8848/health >/dev/null 2>&1; then
  nohup .venv/bin/python mind_server.py --port 8848 --db "$DB" >> /tmp/whale-mind.log 2>&1 &
  for _ in $(seq 30); do
    curl -sf -m 1 http://127.0.0.1:8848/health >/dev/null 2>&1 && break
    sleep 0.5
  done
fi
exec .venv/bin/python whale_mcp.py
