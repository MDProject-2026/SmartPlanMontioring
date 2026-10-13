@echo off
cd /d "%~dp0\.."
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
set SMART_PLANT_HOST=0.0.0.0
python -m backend.app
