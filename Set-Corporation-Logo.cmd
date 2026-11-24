@echo off
rem Give a corporation a PNG logo. Double-click and answer the questions,
rem or run: Set-Corporation-Logo.cmd set C:\EVE\evejs 98000002 logo.png
where py >nul 2>nul
if %errorlevel%==0 (py -3 "%~dp0corp_logo.py" %*) else (python "%~dp0corp_logo.py" %*)
