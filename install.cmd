@echo off
rem Corporation Logos installer: double-click and answer the questions.
where py >nul 2>nul
if %errorlevel%==0 (py -3 "%~dp0install.py" %*) else (python "%~dp0install.py" %*)
