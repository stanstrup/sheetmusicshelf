# Copy the built APK to where the server can hand it out, with a small
# manifest beside it so the install page and the app can tell what is on offer.
#
# In PowerShell rather than in the .cmd: reading a version out of aapt means
# quoting a regex inside a for /f inside cmd, and that fight is not worth
# having twice.
param(
    [string] $ApkRoot = 'Z:\Books\SheetMusic\_app',
    [string] $Apk,
    [string] $Aapt
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Apk)) {
    Write-Error "No APK at $Apk. Run build.cmd first."
}

$badging = & $Aapt dump badging $Apk
$code = [regex]::Match($badging -join "`n", "versionCode='(\d+)'").Groups[1].Value
$name = [regex]::Match($badging -join "`n", "versionName='([^']+)'").Groups[1].Value

if (-not $code) { Write-Error 'Could not read a versionCode from the APK.' }

New-Item -ItemType Directory -Force -Path $ApkRoot | Out-Null
Copy-Item -Force $Apk (Join-Path $ApkRoot 'sheetmusicshelf.apk')

@{
    versionCode = [int] $code
    versionName = $name
    builtAt     = (Get-Date -Format 'yyyy-MM-dd HH:mm')
} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $ApkRoot 'version.json')

$size = [math]::Round((Get-Item (Join-Path $ApkRoot 'sheetmusicshelf.apk')).Length / 1MB, 1)
Write-Output ''
Write-Output "Published $name (code $code, $size MB) to $ApkRoot"
Write-Output ''
Write-Output 'On the tablet, open:  http://192.168.1.9:8014/app'
Write-Output 'Press Install. Updating later is the same page again.'
