@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PORT=9222"
if not "%~1"=="" set "PORT=%~1"

set "TARGET_URL=https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx"
set "BROWSER_EXE="
set "BROWSER_KIND=chrome"
set "USER_DATA_DIR="

call :find_browser
if not defined BROWSER_EXE goto :no_browser

echo [INFO] Browser: !BROWSER_EXE!
echo [INFO] Debug port: !PORT!

call :browser_user_data_dir
call :port_open
if not errorlevel 1 goto :already_ready

if not exist "!USER_DATA_DIR!" mkdir "!USER_DATA_DIR!" >nul 2>nul

echo [1/3] Launching browser with remote debugging...
echo       Debug profile dir: !USER_DATA_DIR!
start "" "!BROWSER_EXE!" --remote-debugging-port=!PORT! --user-data-dir="!USER_DATA_DIR!" --new-window --no-first-run --no-default-browser-check "!TARGET_URL!"

echo [2/3] Waiting for debug port...
call :wait_port
if errorlevel 1 goto :port_failed

echo [3/3] Ready.
echo       Finish login, Cloudflare, and session selection in the debug browser window.
echo       Stop on the booking.aspx rules page, then go back to the Web UI and press Start Takeover.
pause
exit /b 0

:no_browser
echo [ERROR] Chrome or Edge was not found.
echo         Install Chrome, or make sure chrome.exe is available in PATH.
pause
exit /b 1

:already_ready
echo [OK] 127.0.0.1:!PORT! is already listening.
echo      Chrome remote debugging is ready.
echo      Go back to the Web UI and press Start Takeover.
pause
exit /b 0

:port_failed
echo [ERROR] Browser started, but port !PORT! did not open.
echo         Chrome likely ignored the debugging flags, or local security software blocked the port.
echo         Also check whether local security software blocked the debug port.
pause
exit /b 1

:find_browser
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LocalAppData%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
    "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
    "%LocalAppData%\Microsoft\Edge\Application\msedge.exe"
) do (
    if not defined BROWSER_EXE if exist "%%~P" (
        set "BROWSER_EXE=%%~P"
        echo %%~P | findstr /I "msedge.exe" >nul && set "BROWSER_KIND=edge"
    )
)

if not defined BROWSER_EXE (
    for /f "delims=" %%P in ('where chrome 2^>nul') do (
        if not defined BROWSER_EXE (
            set "BROWSER_EXE=%%P"
            set "BROWSER_KIND=chrome"
        )
    )
)

if not defined BROWSER_EXE (
    for /f "delims=" %%P in ('where msedge 2^>nul') do (
        if not defined BROWSER_EXE (
            set "BROWSER_EXE=%%P"
            set "BROWSER_KIND=edge"
        )
    )
)
exit /b 0

:browser_user_data_dir
if /I "!BROWSER_KIND!"=="edge" (
    set "USER_DATA_DIR=%LocalAppData%\ticket-bot\edge-debug-profile"
) else (
    set "USER_DATA_DIR=%LocalAppData%\ticket-bot\chrome-debug-profile"
)
exit /b 0

:port_open
netstat -ano | findstr /R /C:":!PORT! .*LISTENING" >nul 2>nul
if not errorlevel 1 exit /b 0
exit /b 1

:wait_port
set /a RETRY=0
:wait_port_loop
call :port_open
if not errorlevel 1 exit /b 0
set /a RETRY+=1
if !RETRY! GEQ 24 exit /b 1
timeout /t 1 /nobreak >nul
goto :wait_port_loop
