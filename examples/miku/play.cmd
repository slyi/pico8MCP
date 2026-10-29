@echo off
setlocal
cd /d "%~dp0"
set "MIKU_PLAY=%~dp0play.py"

where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 "%MIKU_PLAY%" %*
set "MIKU_RESULT=%ERRORLEVEL%"
goto finished

:try_python
where python >nul 2>&1
if errorlevel 1 goto no_python
python "%MIKU_PLAY%" %*
set "MIKU_RESULT=%ERRORLEVEL%"
goto finished

:no_python
echo Python could not be found. Install Python 3 and try again.
set "MIKU_RESULT=1"

:finished
if not "%MIKU_RESULT%"=="0" (
 echo.
 echo The demo could not start. See the message above.
 pause
)
exit /b %MIKU_RESULT%
