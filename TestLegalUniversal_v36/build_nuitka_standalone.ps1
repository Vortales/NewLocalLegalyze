# =============================================================================
#  Legalyze v36 — сборка Nuitka (standalone), PowerShell
# -----------------------------------------------------------------------------
#  Запуск из папки версии:
#      powershell -NoProfile -ExecutionPolicy Bypass -File .\build_nuitka_standalone.ps1
#  или просто в PowerShell:
#      .\build_nuitka_standalone.ps1
#
#  Скрипт сам выбирает компилятор C:
#    * есть Visual Studio с C++ (vswhere находит VC.Tools) -> --msvc=latest
#    * нет MSVC                                           -> --mingw64 (Nuitka
#      скачает MinGW64 сам; работает для Python 3.12 и ниже)
#  Если нужен zig: .\build_nuitka_standalone.ps1 -Zig
#  Если нужен строгий MSVC и никаких загрузок: .\build_nuitka_standalone.ps1 -Msvc
# =============================================================================
param(
    [switch]$Msvc,
    [switch]$MinGW,
    [switch]$Zig,
    [switch]$NoLto,
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Fail($message) {
    Write-Host ""
    Write-Host "ОШИБКА: $message" -ForegroundColor Red
    exit 1
}

# --- 1. Python ---------------------------------------------------------------
$python = "python"
if (Test-Path ".\.venv\Scripts\python.exe") { $python = ".\.venv\Scripts\python.exe" }

& $python -c "import sys, struct; assert sys.platform=='win32', 'Windows only'; assert struct.calcsize('P')==8, 'нужен 64-битный Python'"
if ($LASTEXITCODE -ne 0) { Fail "нужен 64-битный Python для Windows (см. выше)." }

$pyVersion = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])"
Write-Host "Python: $pyVersion ($python)" -ForegroundColor Cyan

# --- 2. Зависимости сборки ---------------------------------------------------
& $python -m nuitka --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Nuitka не установлена — ставлю requirements-build.txt…" -ForegroundColor Yellow
    & $python -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { Fail "не удалось установить Nuitka (requirements-build.txt)." }
    & $python -m nuitka --version *> $null
    if ($LASTEXITCODE -ne 0) { Fail "Nuitka не установилась." }
}
$nuitkaLine = (& $python -m nuitka --version | Select-String "Version").ToString()
Write-Host $nuitkaLine.Trim() -ForegroundColor Cyan

# --- 3. Выбор компилятора C --------------------------------------------------
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$msvcPath = ""
if (Test-Path $vswhere) {
    $msvcPath = (& $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath) 2>$null
}

$compilerArgs = @()
if ($Zig) {
    $compilerArgs = @("--zig")
    Write-Host "Компилятор: zig (Nuitka скачает сама)" -ForegroundColor Cyan
}
elseif ($Msvc) {
    if (-not $msvcPath) {
        Fail "MSVC не найден. Установите 'Visual Studio Build Tools' с компонентом 'Desktop development with C++', либо запустите без -Msvc (будет --mingw64)."
    }
    $compilerArgs = @("--msvc=latest")
    Write-Host "Компилятор: MSVC ($msvcPath)" -ForegroundColor Cyan
}
elseif ($MinGW) {
    $compilerArgs = @("--mingw64")
    Write-Host "Компилятор: MinGW64 (Nuitka скачает сама)" -ForegroundColor Cyan
}
elseif ($msvcPath) {
    $compilerArgs = @("--msvc=latest")
    Write-Host "Компилятор: MSVC ($msvcPath)" -ForegroundColor Cyan
}
else {
    if ([version]$pyVersion -gt [version]"3.13") {
        Fail ("MSVC не найден, а --mingw64 поддерживается только для Python 3.12 и ниже " +
              "(у вас $pyVersion). Установите Visual Studio Build Tools с C++, затем повторите.")
    }
    $compilerArgs = @("--mingw64")
    Write-Host "MSVC не найден -> компилятор: MinGW64 (Nuitka скачает сама)" -ForegroundColor Yellow
}

# --- 4. Ключи сборки ---------------------------------------------------------
$mode = if ($OneFile) { "--mode=onefile" } else { "--mode=standalone" }
$lto = if ($NoLto) { @() } else { @("--lto=yes") }

$buildArgs = @(
    "-m", "nuitka",
    $mode
) + $compilerArgs + $lto + @(
    "--enable-plugin=pyqt6",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=icon.ico",

    # Пакеты с динамическим импортом
    "--include-package=requests",
    "--include-package=urllib3",
    "--include-package=websocket",
    "--include-package=cryptography",
    "--include-package=fpdf",
    "--include-package=PIL",

    # Собственные модули (подстраховка: они и так видны компилятору)
    "--include-module=machine_profile",
    "--include-module=browser_trace",
    "--include-module=web_compat",
    "--include-module=diagnostics",
    "--include-module=storage_paths",
    "--include-module=native_browser",
    "--include-module=postanovlenie_data",
    "--include-module=postanovlenie_ui",
    "--include-module=postanovlenie_prokuror_data",
    "--include-module=postanovlenie_prokuror_ui",
    "--include-module=doc_render",
    "--include-module=win32_embed",
    "--include-module=win32_hotkeys",

    # ВСЕ данные разом: fonts/, reestr/*.json, gerb_lscsd.png, stamp_lscsd.png,
    # DejaVuSans.ttf, FONT-LICENSE.txt. Без этого в собранной программе нет
    # ни герба с печатью, ни устава для проверки постановлений.
    "--include-data-dir=data=data",

    # websocket.policyserver / websocket.server не парсятся под 3.12 и не нужны клиенту
    "--nofollow-import-to=websocket.policyserver",
    "--nofollow-import-to=websocket.server",
    "--nofollow-import-to=tkinter",
    "--nofollow-import-to=PIL.ImageTk",

    # Метаданные версий (importlib.metadata)
    "--include-distribution-metadata=PyQt6",
    "--include-distribution-metadata=PyQt6-Qt6",
    "--include-distribution-metadata=PyQt6-WebEngine",
    "--include-distribution-metadata=requests",
    "--include-distribution-metadata=urllib3",
    "--include-distribution-metadata=websocket-client",
    "--include-distribution-metadata=cryptography",
    "--include-distribution-metadata=fpdf2",
    "--include-distribution-metadata=pillow",

    "--assume-yes-for-downloads",
    "--product-name=Legalyze",
    "--file-description=Legalyze",
    "--company-name=Legalyze",
    "--file-version=1.2.0.0",
    "--product-version=1.2.0.0",
    "--copyright=Legalyze",
    "--report=build/nuitka-report.xml",
    "--output-dir=build",
    "--output-filename=Legalyze.exe",
    "main.py"
)

Write-Host ""
Write-Host "Сборка: $mode$([string]::Join(' ', $compilerArgs))" -ForegroundColor Cyan
Write-Host "Журнал Nuitka пишется в build\ ; это может занять 10-40 минут." -ForegroundColor DarkGray
Write-Host ""

$sw = [Diagnostics.Stopwatch]::StartNew()
& $python @buildArgs
$code = $LASTEXITCODE
$sw.Stop()

if ($code -ne 0) {
    Write-Host ""
    Write-Host "Сборка не удалась (код $code). Что смотреть:" -ForegroundColor Red
    Write-Host "  * build\nuitka-report.xml — полный отчёт;" -ForegroundColor Red
    Write-Host "  * 'cannot locate suitable C compiler' — нет MSVC: запустите без -Msvc (MinGW64)" -ForegroundColor Red
    Write-Host "    или поставьте Visual Studio Build Tools с компонентом 'Desktop development with C++';" -ForegroundColor Red
    Write-Host "  * не запускайте из 'Developer PowerShell for VS' — Nuitka не находит там компилятор." -ForegroundColor Red
    exit $code
}

# --- 5. Проверка результата --------------------------------------------------
$exe = $null
foreach ($candidate in @("build\Legalyze.dist\Legalyze.exe", "build\main.dist\Legalyze.exe")) {
    if (Test-Path $candidate) { $exe = $candidate; break }
}
if (-not $exe) { Fail "Legalyze.exe не найдена в build\*.dist — смотрите вывод Nuitka." }

$dist = Split-Path -Parent $exe
Write-Host ""
Write-Host ("Готово за {0:N1} мин." -f $sw.Elapsed.TotalMinutes) -ForegroundColor Green
Write-Host "  EXE: $exe" -ForegroundColor Green
Write-Host "  Папка: $dist" -ForegroundColor Green

$missing = @()
foreach ($rel in @(
    "data\gerb_lscsd.png",
    "data\stamp_lscsd.png",
    "data\gerb_gov.png",
    "data\stamp_gov.png",
    "data\DejaVuSans.ttf",
    "data\fonts\PTSerif-Regular.ttf",
    "data\fonts\Caveat-Regular.ttf",
    "data\reestr\Устав Los Santos County Sheriff Department.json"
)) {
    if (-not (Test-Path (Join-Path $dist $rel))) { $missing += $rel }
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "ВНИМАНИЕ: в сборке нет ресурсов:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    Write-Host "Герб/печать/шрифты/устав не найдутся — проверьте --include-data-dir=data=data." -ForegroundColor Yellow
    exit 2
}

Write-Host "  Ресурсы на месте: герб, печать, шрифты, устав." -ForegroundColor Green
Write-Host ""
Write-Host "Запуск: $exe" -ForegroundColor Cyan
exit 0
