@echo off
rem Python discovery supports python, python3, py -3 and CMDBUF_PYTHON.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0simulation\vcs\bootstrap.ps1" %*
exit /b %errorlevel%
