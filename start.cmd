@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 goto location_failed
if not exist "tools\bootstrap_windows.py" goto incomplete_package
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 goto use_venv
)
py -3.12 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto use_py312
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto use_python
echo Python 3.11+ was not found. Install Python 3.12 from:
echo https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH", then double-click start.cmd again.
goto failed

:use_venv
".venv\Scripts\python.exe" tools\bootstrap_windows.py
goto finished
:use_py312
py -3.12 tools\bootstrap_windows.py
goto finished
:use_py
py -3 tools\bootstrap_windows.py
goto finished
:use_python
python tools\bootstrap_windows.py
goto finished

:finished
if errorlevel 1 goto failed
popd
exit /b 0

:incomplete_package
echo The startup helper is missing. Extract all files into this folder.
goto failed
:location_failed
echo Cannot open the application folder. Extract the ZIP before starting.
pause
exit /b 1
:failed
echo.
echo Setup or startup failed. The message above explains the next step.
echo Do not send your .env file or any API keys when sharing an error.
pause
popd
exit /b 1
