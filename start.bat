@echo off
rem Start the NSE Dividend Tracker. Extra arguments are passed to run.py.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run.py %*
  goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
  python run.py %*
  goto :end
)

echo Python 3.11+ was not found. Install it from https://www.python.org/downloads/
exit /b 1

:end
endlocal
