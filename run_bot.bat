@echo off
REM ============================================================
REM  Start the trading bot (DRY-RUN by default - no real orders)
REM  Double-click to run. Keep MetaTrader 5 open and logged in.
REM ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [X] Setup has not been done yet.
  echo     Double-click  setup_and_test.bat  first, then come back here.
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo   Starting the bot in DRY-RUN mode.
echo   It will LOG the trades it would make, but send NO orders.
echo   Keep MetaTrader 5 open and logged in.
echo   Press  Ctrl + C  in this window to stop the bot.
echo ============================================================
echo.
".venv\Scripts\python.exe" -m xauusd_bot.bot
echo.
pause
