@echo off
cd /d "%~dp0"

echo.
echo  ==========================================
echo   GoldKernel SmartMart
echo  ==========================================
echo.

REM Find the machine's local IP for display
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set IP=%%a
    goto :found_ip
)
:found_ip
set IP=%IP: =%

echo  Starting server...
echo  Open in browser:
echo    This PC:        http://localhost:5000
echo    Other devices:  http://%IP%:5000
echo.
echo  Login with: admin / GoldKernel@2026!
echo.
echo  Press Ctrl+C to stop the server.
echo  ==========================================
echo.

.venv\Scripts\python.exe serve.py

pause
