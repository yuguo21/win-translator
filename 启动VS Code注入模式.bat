@echo off
chcp 65001 >nul
setlocal
set "PORT=9400"

rem VS Code 路径探测：可用环境变量 VSCODE_EXE 指定，否则按常见安装位置找
set "EXE=%VSCODE_EXE%"
if not defined EXE (
  for %%P in (
    "%ProgramFiles%\Microsoft VS Code\Code.exe"
    "%ProgramFiles(x86)%\Microsoft VS Code\Code.exe"
    "%LocalAppData%\Programs\Microsoft VS Code\Code.exe"
    "D:\WorkSpace\Microsoft VS Code\Code.exe"
  ) do (
    if not defined EXE if exist %%P set "EXE=%%~P"
  )
)

if not defined EXE (
  echo [错误] 没找到 VS Code。
  echo        请先设置环境变量 VSCODE_EXE 指向 Code.exe，例如：
  echo        set VSCODE_EXE=D:\Apps\Microsoft VS Code\Code.exe
  pause
  exit /b 1
)

tasklist /fi "imagename eq Code.exe" 2>nul | find /i "Code.exe" >nul
if not errorlevel 1 (
  echo [提示] VS Code 正在运行。
  echo        Electron 只在启动那一刻开调试端口，
  echo        请先完全退出 VS Code ^(所有窗口^)，再运行本脚本。
  echo.
  pause
  exit /b 1
)

echo 正在启动 VS Code（调试端口 %PORT%）...
echo     %EXE%
start "" "%EXE%" --remote-debugging-port=%PORT%
echo.
echo 启动后请在翻译工具里选「Windows UI」模式并点「开始翻译」。
timeout /t 3 >nul
exit /b 0
