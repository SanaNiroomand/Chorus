@echo off
REM Start Chorus and open it in your browser. Double-click this file, or run it from a terminal.
cd /d "%~dp0"

REM First run only: install dependencies (uncomment the next line if you haven't yet)
REM python -m pip install -r requirements.txt

start "Chorus server" cmd /k python -m uvicorn server:app --port 8000
timeout /t 2 /nobreak >nul
start "" http://localhost:8000
