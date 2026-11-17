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
  $env:VITE_AUTH_MODE = "email_otp"
  $env:VITE_TRADING_BRAIN_API_URL = "https://$Domain"
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "Dashboard build failed; nothing was uploaded." }
} finally {
  Pop-Location
}

$bucket = "gs://$Project-dashboard"
& $Gcloud --project $Project storage rsync -r --delete-unmatched-destination-objects "$FrontendDir\dist" $bucket
if ($LASTEXITCODE -ne 0) { throw "Upload to $bucket failed." }
& $Gcloud --project $Project compute ssh $Vm --zone $Zone --command "sudo gsutil -m rsync -r -d $bucket /opt/trading_brain/frontend-dist && sudo chmod -R a+rX /opt/trading_brain/frontend-dist"
if ($LASTEXITCODE -ne 0) { throw "The VM did not sync the dashboard." }
Write-Host "Dashboard deployed. Caddy serves the new files without a restart."
