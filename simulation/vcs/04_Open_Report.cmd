@echo off
setlocal
pushd "%~dp0\..\.."
call make.cmd open-report %*
set "eda_rc=%ERRORLEVEL%"
popd
if not "%eda_rc%"=="0" pause
exit /b %eda_rc%
