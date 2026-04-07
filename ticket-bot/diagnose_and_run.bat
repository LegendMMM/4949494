@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo ticket-bot web startup
echo ============================================================
echo.

echo [1] Working directory: %CD%

echo [2] Python check...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] python not found. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)
python --version
echo.

echo [3] Virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv...
    python -m venv .venv
)

echo [4] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -e .
if %errorlevel% neq 0 (
    echo [ERROR] pip install -e . failed
    pause
    exit /b 1
)
echo.

echo [5] Starting web UI...
echo Open: http://127.0.0.1:5000
echo Press Ctrl+C to stop
echo.
".venv\Scripts\python.exe" -m ticket_bot web
pause
