<#
.SYNOPSIS
    Submits a realistic AuthenticAttest lead to the Vaani CRM Public Leads API.

.DESCRIPTION
    Runs the AuthenticAttest lead submission test using the generated API key.
    Automatically queries the database for the active AuthenticAttest API credentials,
    or forwards to the local dev server.

.EXAMPLE
    .\scripts\submit_authenticattest_lead.ps1

.EXAMPLE
    .\scripts\submit_authenticattest_lead.ps1 -Duplicate

.EXAMPLE
    .\scripts\submit_authenticattest_lead.ps1 -Server "http://127.0.0.1:8000"
#>

[CmdletBinding()]
param(
    [string]$Name = "Dr. Ananya Iyer",
    [string]$Phone = "",
    [string]$Email = "ananya.iyer@example.com",
    [string]$Service = "APOSTILLE",
    [string]$Campaign = "uae-embassy-attestation",
    [string]$Location = "Bengaluru, Karnataka",
    [string]$Notes = "Requires MEA Apostille and UAE Embassy attestation for MBBS degree & transcript.",
    [string]$Origin = "https://authenticattest.com",
    [string]$ApiKey = "",
    [string]$Server = "",
    [switch]$DryRun,
    [switch]$Duplicate
)

$pythonExe = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

$scriptPath = Join-Path $PSScriptRoot "submit_authenticattest_lead.py"

$argList = @($scriptPath)
if ($Name) { $argList += "--name", $Name }
if ($Phone) { $argList += "--phone", $Phone }
if ($Email) { $argList += "--email", $Email }
if ($Service) { $argList += "--service", $Service }
if ($Campaign) { $argList += "--campaign", $Campaign }
if ($Location) { $argList += "--location", $Location }
if ($Notes) { $argList += "--notes", $Notes }
if ($Origin) { $argList += "--origin", $Origin }
if ($ApiKey) { $argList += "--api-key", $ApiKey }
if ($Server) { $argList += "--server", $Server }
if ($DryRun) { $argList += "--dry-run" }
if ($Duplicate) { $argList += "--duplicate" }

& $pythonExe $argList
