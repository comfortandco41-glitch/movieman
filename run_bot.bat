@echo off
echo Starting Movie Man Bot...
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found. Creating .venv...
    where py >nul 2>nul && (
        py -3.13 -m venv .venv 2>nul || py -m venv .venv
    ) || (
        python -m venv .venv
    )
    call .venv\Scripts\activate.bat
    echo Installing dependencies...
    pip install -r requirements.txt
    playwright install chromium
) else (
    call .venv\Scripts\activate.bat
)

python -m app.main
pause
