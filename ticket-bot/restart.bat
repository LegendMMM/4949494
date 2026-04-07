@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1] Kill old listener on port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do (
    echo     kill PID: %%a
    taskkill /F /PID %%a >nul 2>nul
)
timeout /t 2 /nobreak >nul

echo [2] Ensure virtual environment...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

echo [3] Install latest local code...
".venv\Scripts\python.exe" -m pip install -e .
if %errorlevel% neq 0 (
    echo [ERROR] pip install -e . failed
    pause
    exit /b 1
)

echo [4] Restarting web UI...
echo Open: http://127.0.0.1:5000
echo.
".venv\Scripts\python.exe" -m ticket_bot web
pause
