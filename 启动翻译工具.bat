@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先用 Windows 的 py 启动器，没有再退回 python
set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% main.py
if errorlevel 1 (
  echo.
  echo [提示] 启动失败。请确认已安装 Python 3.10+ 以及依赖：
  echo         pip install mss pillow winsdk rapidfuzz
  pause
)
