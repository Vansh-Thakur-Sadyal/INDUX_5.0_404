@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Creating the project Python 3.12 environment...
    py -3.12 -m venv .venv
    if errorlevel 1 goto python_error
)

"%PYTHON%" -c "import fastapi, uvicorn, requests, numpy, cv2, sklearn, scipy, pulp" >nul 2>nul
if errorlevel 1 (
    echo Installing or repairing project dependencies...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto dependency_error
)

echo Starting the farm dashboard and API...
echo Keep this window open while using the app.
echo When both servers are ready, open http://127.0.0.1:8000/index.html
echo.
"%PYTHON%" run_app.py
set "APP_EXIT=%ERRORLEVEL%"
echo.
if not "%APP_EXIT%"=="0" echo The app stopped with exit code %APP_EXIT%. Review the error above.
pause
exit /b %APP_EXIT%

:python_error
echo.
echo Python 3.12 is required. Install Python 3.12 and enable the "py" launcher,
echo then run this file again.
pause
exit /b 1

:dependency_error
echo.
echo Dependency installation failed. Check your internet connection and the
echo error above, then run this file again.
pause
exit /b 1
