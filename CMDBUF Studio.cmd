@echo off
cd /d "%~dp0"
call "%~dp0make.cmd" gui
if errorlevel 1 pause
