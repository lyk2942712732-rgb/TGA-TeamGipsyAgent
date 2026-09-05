@echo off
chcp 65001 >nul
setlocal

if "%~1"=="" set "TGA_ARGS=--check-only"
if not "%~1"=="" set "TGA_ARGS=%*"

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 "%~dp0deploy.py" %TGA_ARGS%
    exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo 未找到 Python。请安装 Python 3.11 或更高版本。
    exit /b 1
)

python "%~dp0deploy.py" %TGA_ARGS%
exit /b %errorlevel%
