@echo off
rem ===========================================================================
rem  Legalyze v36 - сборка Nuitka (standalone). Обёртка над PS1-скриптом:
rem  он сам выбирает компилятор C (MSVC, если установлен, иначе MinGW64).
rem
rem  Запуск:  build_nuitka_standalone.bat
rem  Варианты: build_nuitka_standalone.bat -Msvc   (только Visual Studio)
rem            build_nuitka_standalone.bat -Zig    (компилятор zig)
rem ===========================================================================
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_nuitka_standalone.ps1" %*
if errorlevel 1 (
    echo.
    echo Сборка завершилась с ошибкой. Подробности выше и в build\nuitka-report.xml.
    exit /b 1
)
exit /b 0
