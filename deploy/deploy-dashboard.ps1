# Build the dashboard on this PC and upload it to the server.
# Usage (from trading_brain\deploy):  .\deploy-dashboard.ps1 -Server ubuntu@34.148.191.229
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [string]$Domain = "trading-brain.duckdns.org",
  [string]$FrontendDir = "$PSScriptRoot\..\..\frontend",
  [string]$KeyPath = "$HOME\.ssh\trading_gcp"
)

$ErrorActionPreference = "Stop"
Push-Location $FrontendDir
try {
  $env:VITE_USE_MOCK_DATA = "false"
  $env:VITE_AUTH_MODE = "email_otp"
  $env:VITE_TRADING_BRAIN_API_URL = "https://$Domain"
  npm run build
  Remove-Item -Recurse -Force "dist\__capture-seed.html" -ErrorAction SilentlyContinue
} finally {
  Pop-Location
}

$dist = "$FrontendDir\dist"
ssh -i $KeyPath $Server "rm -rf /opt/trading_brain/frontend-dist && mkdir -p /opt/trading_brain/frontend-dist"
scp -r -i $KeyPath "$dist\*" "${Server}:/opt/trading_brain/frontend-dist/"
ssh -i $KeyPath $Server "chmod -R a+rX /opt/trading_brain/frontend-dist && ls /opt/trading_brain/frontend-dist"
Write-Host "Dashboard uploaded. Caddy serves the new files without a restart."
