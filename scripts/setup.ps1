param(
    [switch]$Configure,
    [switch]$InstallTask,
    [switch]$RemoveTask
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$VenvPythonW = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
$TaskName = "OLX Market Monitor"

if ($RemoveTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Задача '$TaskName' удалена."
    exit 0
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        & py -3.12 -m venv (Join-Path $RepoRoot ".venv")
        if ($LASTEXITCODE -ne 0) { throw "Не удалось запустить Python 3.12." }
    } else {
        $Python = Get-Command python -ErrorAction Stop
        & $Python.Source -m venv (Join-Path $RepoRoot ".venv")
        if ($LASTEXITCODE -ne 0) { throw "Не удалось создать виртуальное окружение." }
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Не удалось создать .venv. Установите Python 3.12 и повторите настройку."
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Не удалось обновить pip." }
& $VenvPython -m pip install -e $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить OLX Market Monitor." }

if ($Configure) {
    & $VenvPython -m olx_monitor configure
    if ($LASTEXITCODE -ne 0) { throw "Настройка OLX Market Monitor завершилась ошибкой." }
}

if ($InstallTask) {
    if (-not (Test-Path -LiteralPath $VenvPythonW)) {
        throw "Не найден pythonw.exe в виртуальном окружении."
    }
    $CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $DataHome = Join-Path $env:LOCALAPPDATA "OLXMarketMonitor"
    $TaskAction = New-ScheduledTaskAction `
        -Execute $VenvPythonW `
        -Argument "-m olx_monitor run --home `"$DataHome`"" `
        -WorkingDirectory $RepoRoot
    $TaskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
    $TaskPrincipal = New-ScheduledTaskPrincipal `
        -UserId $CurrentUser `
        -LogonType Interactive `
        -RunLevel Limited
    $TaskSettings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 5) `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $TaskAction `
        -Trigger $TaskTrigger `
        -Principal $TaskPrincipal `
        -Settings $TaskSettings `
        -Description "Личный монитор объявлений OLX.kz; старт после входа владельца в Windows." `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Автозапуск '$TaskName' установлен и запущен для $CurrentUser."
}

if (-not $Configure -and -not $InstallTask) {
    Write-Host "Зависимости установлены. Запустите интерактивную настройку командой:"
    Write-Host "  .\.venv\Scripts\python.exe -m olx_monitor configure"
    Write-Host "Для автозапуска после входа в Windows запустите скрипт с -InstallTask."
}
