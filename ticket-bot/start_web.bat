@echo off
chcp 65001 >nul
cd /d "%~dp0"
call diagnose_and_run.bat
