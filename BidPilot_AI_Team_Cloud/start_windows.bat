@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (echo Install Python 3.12 from python.org and re-run. & pause & exit /b 1)
if not exist .venv python -m venv .venv
if errorlevel 1 (echo Virtual environment creation failed. & pause & exit /b 1)
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (echo Dependency installation failed. & pause & exit /b 1)
.venv\Scripts\python setup_local.py
if errorlevel 1 (pause & exit /b 1)
start "BidPilot Worker" cmd /k ".venv\Scripts\python -m app.worker"
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
