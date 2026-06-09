@echo off
REM ============================================================
REM  Trading bot - one-time setup + connection test
REM  Just double-click this file. It does everything for you.
REM ============================================================
cd /d "%~dp0"
echo ============================================================
echo   STEP 1: Setting up the bot and testing the MT5 connection
echo ============================================================
echo.

REM --- Find Python -------------------------------------------------
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE goto nopython
echo Found Python (%PYEXE%).
echo.

REM --- Create a private environment in this folder ----------------
if not exist ".venv\Scripts\python.exe" (
  echo Creating a private environment ^(.venv^) in this folder...
  %PYEXE% -m venv .venv
  if errorlevel 1 goto venvfail
) else (
  echo Private environment already exists - reusing it.
)
echo.

REM --- Install dependencies --------------------------------------
echo Installing the bot's dependencies. This can take a minute...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
echo.

REM --- Run the read-only connection test -------------------------
echo ============================================================
echo   STEP 2: Connection test (READ-ONLY - sends NO orders)
echo   Make sure your MetaTrader 5 app is OPEN and logged in.
echo ============================================================
echo.
".venv\Scripts\python.exe" -m xauusd_bot.connect_test
echo.
echo ------------------------------------------------------------
echo Done. Read the lines above: every check should say [OK].
echo When it does, double-click  run_bot.bat  to start the bot.
echo ------------------------------------------------------------
echo.
pause
exit /b 0

:nopython
echo [X] Python was not found on this computer.
echo.
echo     Install it first (easiest way^):
echo       1. Open the Microsoft Store app
echo       2. Search for  Python 3.12
echo       3. Click Get / Install
echo     Then double-click this file again.
echo.
pause
exit /b 1

:venvfail
echo [X] Could not create the private environment.
echo     Try reinstalling Python from the Microsoft Store, then run this again.
echo.
pause
exit /b 1

:pipfail
echo [X] Installing dependencies failed.
echo     Check your internet connection and run this file again.
echo.
pause
exit /b 1
