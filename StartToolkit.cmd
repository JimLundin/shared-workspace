@echo off
setlocal
set "PYTHONHOME="
set "PYTHONPATH="
start "" /d "%~dp0" "%~dp0.python\pythonw.exe" "%~dp0toolkit.pyw"
