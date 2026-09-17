#requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Package = "avo[all]",
    [string]$Python = "3.13",
    [switch]$NoUpdateShell
)

$ErrorActionPreference = "Stop"

if ($env:AVO_PACKAGE) { $Package = $env:AVO_PACKAGE }
if ($env:AVO_PYTHON_VERSION) { $Python = $env:AVO_PYTHON_VERSION }
if ($env:AVO_UPDATE_SHELL -in @("0", "false", "no", "off")) { $NoUpdateShell = $true }

if ($env:OS -ne "Windows_NT") {
    throw "install.ps1 is for native Windows. Use install.sh on Linux, macOS, or Git Bash."
}

function Invoke-AvoCommand {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $display = "$File " + ($Arguments -join " ")
    Write-Host "  would run: $display"
    if (-not $DryRun) {
        & $File @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE: $display"
        }
    }
}

Write-Host "Avo installer"
Write-Host "  platform: windows"
Write-Host "  package: $Package"
Write-Host "  python: $Python"
if ($DryRun) {
    Write-Host "  mode: dry-run"
}
Write-Host "  scope: user-global (no administrator access)"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "`u{2192} uv not found; it will be installed for this user ..."
    if ($DryRun) {
        Write-Host "  would download: https://astral.sh/uv/install.ps1"
        Write-Host "  would run: uv installer"
        $uvPath = "uv"
    } else {
        $uvInstaller = Join-Path ([System.IO.Path]::GetTempPath()) ("avo-uv-install-{0}.ps1" -f ([guid]::NewGuid()))
        try {
            Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $uvInstaller
            $shell = Get-Command pwsh, powershell -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $shell) { throw "PowerShell executable not found" }
            & $shell.Source -NoProfile -ExecutionPolicy Bypass -File $uvInstaller
        } finally {
            Remove-Item -LiteralPath $uvInstaller -Force -ErrorAction SilentlyContinue
        }
        $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $uv) { throw "uv was installed but is not on PATH; restart PowerShell and retry" }
        $uvPath = $uv.Source
    }
} else {
    $uvPath = $uv.Source
}

Invoke-AvoCommand -File $uvPath -Arguments @("python", "install", $Python)
Invoke-AvoCommand -File $uvPath -Arguments @("tool", "install", "--force", "--python", $Python, $Package)
if (-not $NoUpdateShell) {
    Invoke-AvoCommand -File $uvPath -Arguments @("tool", "update-shell")
}

Write-Host ""
Write-Host "Avo installed successfully as a user-global command."
Write-Host "Run: avo --version"
if (-not $NoUpdateShell) {
    Write-Host "If 'avo' is not found, restart PowerShell so the updated PATH is loaded."
}
