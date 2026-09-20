#requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Version = "latest",
    [string]$BinDir = "",
    [switch]$FromSource,
    [string]$Package = "avo[all]",
    [string]$Python = "3.13",
    [switch]$NoUpdateShell
)

$ErrorActionPreference = "Stop"

if ($env:AVO_VERSION) { $Version = $env:AVO_VERSION }
if ($env:AVO_INSTALL_DIR) { $BinDir = $env:AVO_INSTALL_DIR }
if ($env:AVO_INSTALL_MODE -eq "source") { $FromSource = $true }
if ($env:AVO_PACKAGE) { $Package = $env:AVO_PACKAGE }
if ($env:AVO_PYTHON_VERSION) { $Python = $env:AVO_PYTHON_VERSION }
if ($env:AVO_UPDATE_SHELL -in @("0", "false", "no", "off")) { $NoUpdateShell = $true }

if ($env:OS -ne "Windows_NT") {
    throw "install.ps1 is for native Windows. Use install.sh on Linux, macOS, or Git Bash."
}

if (-not $BinDir) {
    $BinDir = Join-Path $env:LOCALAPPDATA "avo\bin"
}

Write-Host "Avo installer"
Write-Host "  platform: windows"

# -----------------------------------------------------------------------------
# Mode 1: Standalone Binary (Zero Python Required)
# -----------------------------------------------------------------------------
if (-not $FromSource) {
    $cleanVersion = $Version.TrimStart("v")
    $tag = if ($Version -eq "latest") { "latest" } else { "v$cleanVersion" }
    $archiveName = "avo-$cleanVersion-windows-x86_64.zip"

    if ($tag -eq "latest") {
        $downloadUrl = "https://github.com/Fqih/avo/releases/latest/download/$archiveName"
    } else {
        $downloadUrl = "https://github.com/Fqih/avo/releases/download/$tag/$archiveName"
    }

    Write-Host "  mode: standalone (zero Python runtime required)"
    Write-Host "  destination: $BinDir\avo.exe"
    Write-Host "  download: $downloadUrl"

    if ($DryRun) {
        Write-Host "  mode: dry-run (standalone binary, zero-python)"
        Write-Host "  scope: user-global (no administrator access)"
        Write-Host "  would ensure directory: $BinDir"
        Write-Host "  would download: $downloadUrl"
        Write-Host "  would extract: avo.exe -> $BinDir\avo.exe"
        if (-not $NoUpdateShell) {
            Write-Host "  would add to User Environment Path: $BinDir"
        }
        return
    }

    if (-not (Test-Path -LiteralPath $BinDir)) {
        New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    }

    $tempZip = Join-Path ([System.IO.Path]::GetTempPath()) ("avo-install-{0}.zip" -f ([guid]::NewGuid()))
    $tempExtract = Join-Path ([System.IO.Path]::GetTempPath()) ("avo-extract-{0}" -f ([guid]::NewGuid()))

    try {
        Write-Host "`u{2192} Downloading standalone binary archive from GitHub Releases ..."
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tempZip

        Write-Host "`u{2192} Extracting archive ..."
        Expand-Archive -LiteralPath $tempZip -DestinationPath $tempExtract -Force

        $extractedExe = Join-Path $tempExtract "avo.exe"
        if (-not (Test-Path -LiteralPath $extractedExe)) {
            throw "Archive did not contain avo.exe"
        }

        $targetExe = Join-Path $BinDir "avo.exe"
        Copy-Item -LiteralPath $extractedExe -Destination $targetExe -Force
        Write-Host "`u{2713} Standalone binary installed: $targetExe"
    } finally {
        Remove-Item -LiteralPath $tempZip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (-not $NoUpdateShell) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($userPath -notlike "*$BinDir*") {
            Write-Host "`u{2192} Adding $BinDir to User Path environment variable ..."
            $newPath = "$BinDir;$userPath"
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            $env:Path = "$BinDir;$env:Path"
            Write-Host "`u{2713} Added to User Path."
        }
    }

    Write-Host ""
    Write-Host "Avo standalone CLI installed successfully as a user-global command."
    Write-Host "Run: avo --version"
    return
}

# -----------------------------------------------------------------------------
# Mode 2: Source / Python Package via uv (Fallback)
# -----------------------------------------------------------------------------
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

Write-Host "  mode: source / uv tool"
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
