@echo off
chcp 65001 >nul
cd /d "%~dp0"
python inject.py --mods "%~dp0..\mods" %*
pause
