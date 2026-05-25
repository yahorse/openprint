# OpenPrint Installer for Windows
# Run: irm https://raw.githubusercontent.com/yahorse/openprint/main/scripts/install.ps1 | iex

$ErrorActionPreference = "Stop"

Write-Host "=== OpenPrint Installer ===" -ForegroundColor Cyan
Write-Host ""

$installDir = "$env:LOCALAPPDATA\OpenPrint"
$binPath = "$installDir\opp.exe"

# Create install directory
if (-not (Test-Path $installDir)) {
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
}

# Get latest release
Write-Host "[1/3] Fetching latest release..."
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/yahorse/openprint/releases/latest"
$asset = $release.assets | Where-Object { $_.name -like "*windows-amd64*" }

if (-not $asset) {
    Write-Host "Error: No Windows binary found in latest release." -ForegroundColor Red
    Write-Host "Install with pip instead: pip install openprint"
    exit 1
}

# Download
Write-Host "[2/3] Downloading $($asset.name)..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $binPath

# Add to PATH
Write-Host "[3/3] Adding to PATH..."
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$installDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$installDir", "User")
}

Write-Host ""
Write-Host "Done! OpenPrint installed to $binPath" -ForegroundColor Green
Write-Host ""
Write-Host "  Restart your terminal, then:"
Write-Host "    opp discover        # Find printers"
Write-Host "    opp print doc.pdf   # Print a file"
Write-Host "    opp bridge          # Bridge all printers to OPP"
Write-Host ""
