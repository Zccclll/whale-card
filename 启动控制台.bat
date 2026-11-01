@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 如果还没有卡，先造一张假数据的示例卡
if not exist "card.db" if not exist "demo\demo_card.db" (
  echo [1/3] 没有找到卡，正在生成示例卡...
  python tools\make_demo_card.py
  set WHALE_DB=demo\demo_card.db
)

rem 服务没起就拉起来（独立进程，关掉这个窗口也不影响）
echo [2/3] 检查心智服务...
python bridge.py health
if errorlevel 1 (
  echo 服务启动失败，看看上面的报错。
  pause
  exit /b 1
)

rem 开浏览器；用 start 让窗口不占着脚本
echo [3/3] 打开控制台...
start "" msedge --new-window "http://127.0.0.1:8848/console"
if errorlevel 1 start "" "http://127.0.0.1:8848/console"

ping -n 3 127.0.0.1 >nul
exit /b 0
