@echo off
setlocal
pushd "%~dp0\..\.."
call make.cmd coverage %*
set "eda_rc=%ERRORLEVEL%"
popd
if not "%eda_rc%"=="0" pause
exit /b %eda_rc%
