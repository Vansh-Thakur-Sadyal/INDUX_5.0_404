@echo off
REM Saarthi 2.0 - start the backend and serve the dashboard on one origin.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv || goto :err
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :err
)
echo.
echo   Saarthi 2.0  ->  http://127.0.0.1:8010/
echo   API docs     ->  http://127.0.0.1:8010/docs
echo.
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
goto :eof
:err
echo.
echo Setup failed. Make sure Python 3.10+ is installed and on PATH.
pause
