@echo off
REM ============================================================
REM  LMU PIT WALL - stop everything (supervisor, server, tunnel)
REM ============================================================
echo Stopping LMU Pit Wall supervisor, server and tunnel...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'supervisor\.py|server\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
taskkill /F /IM cloudflared.exe >nul 2>&1
echo Done.
timeout /t 3 >nul
