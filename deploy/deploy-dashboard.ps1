# Build the dashboard on this PC, upload it to Cloud Storage, and sync the VM.
# Usage (from trading_brain\deploy):  .\deploy-dashboard.ps1
param(
  [string]$Project = "trading-brain-514921",
  [string]$Zone = "us-east1-c",
  [string]$Vm = "trading-brain",
  [string]$Domain = "trading-brain.duckdns.org",
  [string]$FrontendDir = "$PSScriptRoot\..\..\frontend",
  [string]$Gcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
)

$ErrorActionPreference = "Stop"
Push-Location $FrontendDir
try {
  $env:VITE_USE_MOCK_DATA = "false"
  $env:VITE_AUTH_MODE = "email_otp"
  $env:VITE_TRADING_BRAIN_API_URL = "https://$Domain"
  npm run build
  Remove-Item -Force "dist\__capture-seed.html" -ErrorAction SilentlyContinue
} finally {
  Pop-Location
}

$bucket = "gs://$Project-dashboard"
& $Gcloud --project $Project storage rsync -r -d "$FrontendDir\dist" $bucket
& $Gcloud --project $Project compute ssh $Vm --zone $Zone --command "sudo gsutil -m rsync -r -d $bucket /opt/trading_brain/frontend-dist && sudo chmod -R a+rX /opt/trading_brain/frontend-dist"
Write-Host "Dashboard deployed. Caddy serves the new files without a restart."
