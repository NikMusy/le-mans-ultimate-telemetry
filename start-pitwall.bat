@echo off
REM ============================================================
REM  LMU PIT WALL - start the supervisor (keeps everything alive)
REM  The supervisor starts the tunnel + telemetry server, keeps
REM  them alive, restarts the server when LMU restarts, and
REM  auto-publishes the live link to https://lmu-pitwall.pages.dev/
REM ============================================================
cd /d "%~dp0"
echo Starting LMU Pit Wall supervisor...
echo.
echo It will keep the tunnel + telemetry server alive and auto-update the
echo strategist link. Strategist always uses ONE permanent address:
echo     https://lmu-pitwall.pages.dev/
echo.
start "LMU Pit Wall Supervisor" ".venv\Scripts\python.exe" supervisor.py
echo Launched in its own window. You can close THIS window.
timeout /t 4 >nul
