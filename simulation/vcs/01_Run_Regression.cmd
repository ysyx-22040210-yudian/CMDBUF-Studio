@echo off
setlocal
pushd "%~dp0\..\.."
call make.cmd regress %*
set "eda_rc=%ERRORLEVEL%"
echo.
echo Run make.cmd open-report to download and open the report. Exit code: %eda_rc%
popd
pause
exit /b %eda_rc%
