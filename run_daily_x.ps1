[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$secretPath = 'C:\keiba_scraper\fx_performance\private\secrets\buffer_api_key.credential.xml'
$python = 'C:\Users\Owner\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$script = Join-Path $project 'timesignalfx_daily.py'
$stateDir = Join-Path $project 'state'
$logPath = Join-Path $stateDir 'daily_x.log'
$exitCode = 1
$bstr = [IntPtr]::Zero
$plain = $null

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

function Write-XRunLog {
    param([string]$Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    "[$stamp] $Message" | Add-Content -LiteralPath $logPath -Encoding utf8
}

try {
    Write-XRunLog -Message ("runner_identity={0}; secret_exists={1}; python_exists={2}; script_exists={3}" -f [Security.Principal.WindowsIdentity]::GetCurrent().Name, (Test-Path -LiteralPath $secretPath), (Test-Path -LiteralPath $python), (Test-Path -LiteralPath $script))
    if (-not (Test-Path -LiteralPath $secretPath)) { throw 'Buffer DPAPI credential is missing.' }
    if (-not (Test-Path -LiteralPath $python)) { throw 'Python runtime is missing.' }
    if (-not (Test-Path -LiteralPath $script)) { throw 'Daily X publisher is missing.' }

    Set-Location -LiteralPath $project
    $secure = Import-Clixml -LiteralPath $secretPath
    $channelId = [Environment]::GetEnvironmentVariable('BUFFER_CHANNEL_ID', 'User')
    if ([string]::IsNullOrWhiteSpace($channelId)) { throw 'BUFFER_CHANNEL_ID is missing.' }

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:BUFFER_API_KEY = $plain
    $env:BUFFER_CHANNEL_ID = $channelId

    $output = & $python $script --publish 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-XRunLog -Message $line
        Write-Output $line
    }
}
catch {
    $message = $_.Exception.Message -replace '[\r\n]+', ' '
    Write-XRunLog -Message ("runner_failed: {0}: {1}" -f $_.Exception.GetType().Name, $message)
    [Console]::Error.WriteLine("TimeSignalFX X publisher failed: {0}" -f $message)
    $exitCode = 1
}
finally {
    Remove-Item Env:BUFFER_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BUFFER_CHANNEL_ID -ErrorAction SilentlyContinue
    $plain = $null
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

exit $exitCode
