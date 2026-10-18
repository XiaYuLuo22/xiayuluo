@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install Python 3.11+, then run: py -3 -m venv .venv
  popd
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -c requirements.lock -e ".[app,desktop,build]"
if errorlevel 1 (
  popd
  exit /b 1
)
".venv\Scripts\python.exe" tools\build_desktop.py %*
set "assistant_build_exit=%errorlevel%"
popd
exit /b %assistant_build_exit%
