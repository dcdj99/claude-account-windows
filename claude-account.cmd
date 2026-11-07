@echo off
setlocal DisableDelayedExpansion
python -I -B "%~dp0claude_account.py" %*
exit /b %errorlevel%
