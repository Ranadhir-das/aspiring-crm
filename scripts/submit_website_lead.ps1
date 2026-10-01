<#
.SYNOPSIS
    Submits a test website lead to the Vaani CRM Public Leads API.

.DESCRIPTION
    Sends a JSON payload to /api/v1/public/leads/ to simulate a website form submission.
    Supports custom names, phone numbers, services, sources, campaigns, and API keys.

.EXAMPLE
    .\submit_website_lead.ps1 -Name "Aarav Sharma" -Phone "+91 98765 43210" -Service "MBBS"
    
.EXAMPLE
    .\submit_website_lead.ps1 -Phone "9876543210" -Service "MBA" -ApiKey "ws_your_secret_key"
#>

[CmdletBinding()]
param(
    [string]$ServerUrl = "http://127.0.0.1:8000",
    [string]$Name = "Test Student",
    [string]$Phone = "+91 98765 00123",
    [string]$Email = "test.student@example.com",
    [string]$Service = "MBBS",
    [string]$Source = "website",
    [string]$Campaign = "website-test-campaign",
    [string]$Location = "New Delhi",
    [string]$Notes = "Enquiry submitted via test script",
    [string]$ApiKey = ""
)

$endpoint = "$ServerUrl/api/v1/public/leads/"

$payload = @{
    name     = $Name
    phone    = $Phone
    email    = $Email
    service  = $Service
    source   = $Source
    campaign = $Campaign
    location = $Location
    notes    = $Notes
}

$headers = @{
    "Content-Type" = "application/json"
}

if ($ApiKey) {
    $headers["X-Api-Key"] = $ApiKey
}

$jsonBody = $payload | ConvertTo-Json -Compress

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Submitting Website Lead to Vaani CRM API" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "URL:     $endpoint"
Write-Host "Payload: $jsonBody"
Write-Host ""

try {
    $response = Invoke-RestMethod -Uri $endpoint -Method Post -Headers $headers -Body $jsonBody
    Write-Host "[SUCCESS] Lead Submission Accepted (HTTP 202)" -ForegroundColor Green
    Write-Host "Detail:        $($response.detail)"
    if ($null -ne $response.lead_id) {
        Write-Host "Lead ID:       $($response.lead_id)"
    }
    if ($null -ne $response.is_duplicate) {
        Write-Host "Is Duplicate:  $($response.is_duplicate)"
    }
    Write-Host ""
    Write-Host "Next Verification Steps:" -ForegroundColor Yellow
    Write-Host "1. Check Lead Routing view in CRM Web UI: $ServerUrl/leads/routing/"
    Write-Host "2. Eligible callers for service '$Service' can now see this in Caller App -> 'Available Leads'."
    Write-Host "3. First caller to tap 'Call' claims ownership and dials."
}
catch {
    Write-Host "[ERROR] Request Failed: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Response) {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $respBody = $reader.ReadToEnd()
        Write-Host "Status Code: $([int]$_.Exception.Response.StatusCode)" -ForegroundColor Red
        Write-Host "Response Body: $respBody" -ForegroundColor Yellow
    }
}
