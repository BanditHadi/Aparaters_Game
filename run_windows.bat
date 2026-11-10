@echo off
REM Streamer Boost Live - Windows launcher
cd /d "%~dp0"

if not exist venv (
  echo Creating virtual environment...
  python -m venv venv
  call venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call venv\Scripts\activate.bat
)

REM --- Ensure Playwright is installed (for Aparat) ---
python -c "import playwright" 2>nul
if errorlevel 1 (
  echo Installing Playwright...
  python -m pip install playwright
)

REM --- Ensure Chromium is downloaded ---
if not exist "%LOCALAPPDATA%\ms-playwright" (
  echo Downloading Chromium for Playwright...
  set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
  python -m playwright install chromium
)

echo.
echo  =====================================================
echo   STREAMER BOOST LIVE - Aparat launcher
echo  =====================================================
echo.
echo  1. Live mode (Aparat)
echo  2. Demo mode (fake chat)
echo  3. Off (only ingest API / userscript)
echo.
set /p MODE=Choose [1/2/3, default=1]: 

if "%MODE%"=="2" goto demo
if "%MODE%"=="3" goto off

:aparat
echo.
echo  Paste your Aparat live page URL
echo  (e.g. https://www.aparat.com/NAME/live)
set /p APARAT_URL=
if "%APARAT_URL%"=="" (
  echo.
  echo  ERROR: no URL given.  Starting DEMO mode instead.
  echo.
  python main.py --source demo
) else (
  python main.py --source aparat --aparat-url "%APARAT_URL%"
)
goto end

:demo
python main.py --source demo
goto end

:off
python main.py --source off
goto end

:end
pause