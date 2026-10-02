@echo off
rem One way in, on Windows. Works from cmd.exe, from PowerShell (.\slopgen.bat web)
rem and from a shortcut.
rem
rem   slopgen.bat web                 the browser GUI
rem   slopgen.bat info ru facts -n 3  anything the CLI takes
rem   slopgen.bat --setup             install everything, run nothing
rem   slopgen.bat --check             what is and is not in place
rem   slopgen.bat --yes web           and never ask before installing
rem
rem This file finds a Python and nothing else. Every decision — which interpreter,
rem which virtualenv, where ffmpeg comes from — belongs to scripts\bootstrap.py, so
rem that it is made the same way here and in slopgen.sh.

setlocal EnableExtensions
cd /d "%~dp0"

rem The venv's own interpreter once there is one: it is guaranteed new enough, and
rem after the first run this needs nothing from the system. `py` before `python`
rem because a machine with no Python has a `python` that is a Store advertisement.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (where /q py && set "PY=py")
if not defined PY (where /q python3 && set "PY=python3")
if not defined PY (where /q python && set "PY=python")

if not defined PY (
    echo slopgen: no Python on this machine. Install 3.12 or newer, then run this again:
    echo     winget install Python.Python.3.12
    echo   ...or get it from https://www.python.org/downloads/windows/
    exit /b 1
)

rem --setup and --check are the bootstrap's own business; everything else is an
rem argument for slopgen itself.
if "%~1"=="--setup" goto bootstrap
if "%~1"=="--check" goto bootstrap

rem Collect the arguments by hand, because --yes belongs to the bootstrap and the rest
rem belongs to slopgen — and `shift` does not touch %*. %1 rather than %~1 keeps the
rem quoting the user typed, which matters for every --scenario in the README.
set "YES="
set "ARGS="
:collect
if "%~1"=="" goto run
if "%~1"=="--yes" (
    set "YES=--yes"
    shift
    goto collect
)
set "ARGS=%ARGS% %1"
shift
goto collect

:run
"%PY%" scripts\bootstrap.py %YES% --run --%ARGS%
exit /b %ERRORLEVEL%

:bootstrap
"%PY%" scripts\bootstrap.py %*
exit /b %ERRORLEVEL%
