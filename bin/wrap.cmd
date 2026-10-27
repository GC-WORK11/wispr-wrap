@echo off
rem wrap — direct-invoke shim when npm's generated .cmd isn't on PATH.
rem Defers to wrap.js (node): bash on POSIX, python wrap_cli.py here.
setlocal
set ROOT=%~dp0..
where node >nul 2>nul
if %errorlevel%==0 (
  node "%ROOT%\bin\wrap.js" %*
  exit /b %errorlevel%
)
rem no node — run the python port directly
set PY=
where python >nul 2>nul && set PY=python
if not defined PY ( where python3 >nul 2>nul && set PY=python3 )
if not defined PY ( where py >nul 2>nul && set PY=py -3 )
if not defined PY (
  echo wrap: node or python required >&2
  exit /b 1
)
%PY% "%ROOT%\server\wrap_cli.py" %*
exit /b %errorlevel%
