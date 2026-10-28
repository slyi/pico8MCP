# GitHub's Windows runner is an administrator. Only fixture provisioning uses
# that token; the Python setup, MCP server and browser run as a standard user.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][ValidateSet('x64','arm64')][string]$Architecture,
    [switch]$Child,
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))

if ($Child) {
    # Start-Process inherits the runner's environment. Resolve these paths
    # through the new account's loaded Windows profile instead.
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $profileKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid"
    $profile = (Get-ItemProperty -LiteralPath $profileKey).ProfileImagePath
    $env:USERPROFILE = [Environment]::ExpandEnvironmentVariables($profile)
    $env:LOCALAPPDATA = Join-Path $env:USERPROFILE 'AppData/Local'
    $env:APPDATA = Join-Path $env:USERPROFILE 'AppData/Roaming'
    $env:TEMP = Join-Path $env:LOCALAPPDATA 'Temp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
    Remove-Item Env:PICO8_MCP_HOME,Env:PICO8_MCP_PROFILE_DIR -ErrorAction SilentlyContinue
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $Python -B (Join-Path $repo 'tests/ci.py') --expect-non-admin --expect-arch $Architecture --out-dir $OutputDirectory
    exit $LASTEXITCODE
}

if ($env:GITHUB_ACTIONS -ne 'true') {
    throw 'Standard-account provisioning is restricted to disposable GitHub Actions runners.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'The CI fixture needs the runner admin token to create its test account.'
}

$username = 'p8ci' + [Guid]::NewGuid().ToString('N').Substring(0,10)
$password = ConvertTo-SecureString ('P8!a' + [Guid]::NewGuid().ToString('N')) -AsPlainText -Force
$account = "$env:COMPUTERNAME\$username"
$scratch = Join-Path $env:PUBLIC ('pico8mcp-ci-' + [Guid]::NewGuid().ToString('N'))
$source = Join-Path $scratch 'source'
$reports = Join-Path $scratch 'reports'
$log = Join-Path $scratch 'stdout.txt'
$errors = Join-Path $scratch 'stderr.txt'
$destination = Join-Path $repo 'debug/ci'
$created = $false
try {
    New-LocalUser -Name $username -Password $password -AccountNeverExpires | Out-Null
    $created = $true
    $users = Get-LocalGroup -SID 'S-1-5-32-545'
    Add-LocalGroupMember -Group $users -Member $username
    # Copy the source to a readable, writable test location, independent of
    # the runner administrator's home directory and checkout ACLs.
    New-Item -ItemType Directory -Path $scratch,$reports -Force | Out-Null
    Copy-Item -LiteralPath $repo -Destination $source -Recurse
    & icacls.exe $scratch /grant "${account}:(OI)(CI)M" /T /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not grant the standard account access to its test fixture.' }
    $script = Join-Path $source '.github/scripts/windows-standard-user.ps1'
    $arguments = @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                   '-File',('"'+$script+'"'),'-Child','-Python',('"'+$Python+'"'),
                   '-Architecture',$Architecture,'-OutputDirectory',('"'+$reports+'"'))
    $credential = [PSCredential]::new($account,$password)
    $process = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList $arguments `
        -Credential $credential -LoadUserProfile -WindowStyle Hidden `
        -WorkingDirectory $source -RedirectStandardOutput $log `
        -RedirectStandardError $errors -PassThru
    $handle = $process.Handle
    if (-not $process.WaitForExit(600000)) {
        Stop-Process -Id $process.Id -Force
        throw 'Standard-account CI exceeded ten minutes.'
    }
    $exitCode = $process.ExitCode
    if ($null -eq $exitCode) { throw 'Could not read the standard-account process exit code.' }
    if ($exitCode -ne 0) { throw "Standard-account tests exited $exitCode." }
}
finally {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    if (Test-Path -LiteralPath $reports) {
        Get-ChildItem -LiteralPath $reports -File | Copy-Item -Destination $destination
    }
    foreach ($file in @($log,$errors)) {
        if (Test-Path -LiteralPath $file) {
            Get-Content -LiteralPath $file
            Copy-Item -LiteralPath $file -Destination $destination
        }
    }
    if ($created) { Remove-LocalUser -Name $username }
    # The disposable runner removes the fixture/profile after the job.
    # Downloaded runtime files are excluded from uploaded artifacts.
}
