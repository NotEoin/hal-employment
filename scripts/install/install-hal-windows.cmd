@echo off
rem Hal Employment installer and repair. Double-click it. No admin rights are needed.
set "HAL_REPO=https://github.com/NotEoin/hal-employment.git"
set "HAL_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = Get-Content -Raw -LiteralPath $env:HAL_SELF; $p = ($t -split '(?m)^#PS1#\r?$', 2)[1]; Invoke-Expression $p" & pause & exit /b
#PS1#
# PowerShell 5.1 from here; ASCII only; every download pinned by version and SHA-256 (scripts/install/pins.toml values, copied in).
# Hal's repository is public, so nothing signs in to GitHub unless the repository cannot be read without it.
# Each step checks whether it is already done, so running this file again repairs an install.
# The full log goes to %APPDATA%\Hal Employment\data\logs\install-<UTC>.log, with no secrets in it.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# the window otherwise says C:\WINDOWS\system32\cmd.exe (checked on a Windows 11 virtual machine)
try { $Host.UI.RawUI.WindowTitle = 'Installing Hal Employment' } catch { }
# PINS-START (written by scripts/install/pins.py from pins.toml)
$Pins = @{
  'uv' = @{ Version = '0.8.22'; Url = 'https://github.com/astral-sh/uv/releases/download/0.8.22/uv-x86_64-pc-windows-msvc.zip'; Sha256 = '5049375aa2a5162f132b2c1cb992e25d42d47d934cab8c174dbe6f60973dcc12' }
  'git' = @{ Version = '2.51.0'; Url = 'https://github.com/git-for-windows/git/releases/download/v2.51.0.windows.1/PortableGit-2.51.0-64-bit.7z.exe'; Sha256 = 'a09b275d51ed3e829128e04cf4168fb54896cf6234bb30fecb8dc96a2bd321fa' }
  'gh' = @{ Version = '2.79.0'; Url = 'https://github.com/cli/cli/releases/download/v2.79.0/gh_2.79.0_windows_amd64.zip'; Sha256 = '0372db012e22db3d737ed592ed49b72fdcd47b46d7e9d7e75476410337bd278d' }
}
# PINS-END
$MinBuild = 17763                  # Windows 10 1809
$MinFreeBytes = 3GB
$HealthWaitSeconds = 60
$LogTries = 30                     # 30 x 100 ms: a scan of a small file ends well within 3 s
$LogWaitMs = 100
$Old = Join-Path $env:USERPROFILE 'HalEmployment'
$HasOld = Test-Path -LiteralPath (Join-Path $Old 'data\jobs.db')
$Total = 6 + [int]$HasOld
$script:Step = 0

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Slug = ($env:HAL_REPO -replace '^https://github.com/', '') -replace '\.git$', ''
$Issues = 'https://github.com/' + $Slug + '/issues'
$P = Join-Path $env:LOCALAPPDATA 'Programs\Hal Employment'
$Tools = Join-Path $P 'tools'
$App = Join-Path $P 'app'
$Logs = Join-Path $env:APPDATA 'Hal Employment\data\logs'
New-Item -ItemType Directory -Force -Path $Tools | Out-Null
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$Log = Join-Path $Logs ('install-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss') + 'Z.log')
$script:LastOut = ''

function Write-Log([string]$Text) {
  # an antivirus scan or another reader can hold the log for a moment; a line that still cannot be written is
  # dropped rather than shown in red or stopping the install (seen on a Windows 11 virtual machine)
  $line = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss') + 'Z ' + $Text
  for ($try = 0; $try -lt $LogTries; $try++) {
    try { Add-Content -LiteralPath $Log -Value $line -ErrorAction Stop; return } catch { Start-Sleep -Milliseconds $LogWaitMs }
  }
}

function Say([string]$Text) {
  Write-Host $Text
  Write-Log $Text
}

function Say-Step([string]$Text) {
  $script:Step++
  Say ('' + $script:Step + ' of ' + $Total + ': ' + $Text)
}

function Stop-Install([string]$Text) {
  Say $Text
  Write-Log 'Stopped.'
  exit 1
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
  # native programs write progress to stderr; with 'Stop' PowerShell 5.1 would treat that as a failure
  $ErrorActionPreference = 'Continue'
  Write-Log ('Running ' + $Exe + ' ' + ($Arguments -join ' '))
  $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
  $code = $LASTEXITCODE
  $script:LastOut = ($out -join "`n")
  Write-Log $script:LastOut
  return $code
}

function Get-Pinned([string]$Name, [string]$Destination) {
  $pin = $Pins[$Name]
  if (-not $pin.Sha256) {
    Stop-Install "This install file is not finished: it has no checksum for $Name. Report it at $Issues."
  }
  if (Test-Path -LiteralPath $Destination) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash -eq $pin.Sha256.ToUpper()) { return }
    Remove-Item -Force -LiteralPath $Destination
  }
  try {
    Invoke-WebRequest -UseBasicParsing -Uri $pin.Url -OutFile $Destination
  } catch {
    Write-Log "$_"
    Stop-Install 'Hal could not reach GitHub. Check the internet connection, then double-click this file again.'
  }
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash -ne $pin.Sha256.ToUpper()) {
    Remove-Item -Force -LiteralPath $Destination
    Stop-Install "The download of $Name was not the file Hal expected, so Hal stopped. Double-click this file again."
  }
  Write-Log "Checked $Name $($pin.Version) against its SHA-256."
}

function Test-HalAnswers([string]$Python) {
  $code = Invoke-Native $Python @('-c', 'import sys; from jobhunt.web import app, auth; sys.exit(0 if app.answering(auth.server_port()) else 1)')
  return ($code -eq 0)
}

# 1. Checks ---------------------------------------------------------------------------------------------
Say 'Installing Hal Employment. This takes about 5 minutes. You do not need to type anything.'
$version = [Environment]::OSVersion.Version
if (-not [Environment]::Is64BitOperatingSystem -or $version.Major -lt 10 -or $version.Build -lt $MinBuild) {
  Stop-Install 'Hal needs 64-bit Windows 10 (version 1809 or newer) or Windows 11.'
}
$drive = Get-PSDrive -Name $env:LOCALAPPDATA.Substring(0, 1)
if ($drive.Free -lt $MinFreeBytes) {
  Stop-Install 'Hal needs 3 GB of free space. Free some space, then double-click this file again.'
}

# 2. Tools: uv and the bundled Git, never another git on PATH, nothing added to PATH ------------------------
Say-Step 'Getting the tools Hal needs'
$UvDir = Join-Path $Tools 'uv'
$Uv = Join-Path $UvDir 'uv.exe'
if (-not (Test-Path -LiteralPath $Uv)) {
  $zip = Join-Path $Tools 'uv.zip'
  Get-Pinned 'uv' $zip
  Expand-Archive -Force -LiteralPath $zip -DestinationPath $UvDir
  $found = Get-ChildItem -LiteralPath $UvDir -Recurse -Filter 'uv.exe' | Select-Object -First 1
  if ($found -and $found.FullName -ne $Uv) { Move-Item -Force -LiteralPath $found.FullName -Destination $Uv }
  Remove-Item -Force -LiteralPath $zip
}
$GitDir = Join-Path $Tools 'git'
$Git = Join-Path $GitDir 'cmd\git.exe'
if (-not (Test-Path -LiteralPath $Git)) {
  $sfx = Join-Path $Tools 'PortableGit.7z.exe'
  Get-Pinned 'git' $sfx
  $unpack = Start-Process -FilePath $sfx -ArgumentList ('-o"' + $GitDir + '" -y') -Wait -PassThru -WindowStyle Hidden
  Remove-Item -Force -LiteralPath $sfx
  if ($unpack.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Git)) {
    Stop-Install 'Hal could not unpack its copy of Git. Double-click this file again.'
  }
}
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools 'python'
$env:UV_CACHE_DIR = Join-Path $Tools 'uv-cache'
$env:UV_NO_MODIFY_PATH = '1'

# 3. Hal's files, with a GitHub sign-in only if they cannot be read without one -----------------------
Say-Step "Getting Hal's files from GitHub"
if (Test-Path -LiteralPath (Join-Path $App '.git')) {
  if ((Invoke-Native $Git @('-C', $App, 'pull', '--ff-only')) -ne 0) {
    Stop-Install 'Hal could not get its newest files. Check the internet connection, then double-click this file again.'
  }
} else {
  # no helper and no prompt: a repository that needs a login must fail here, not wait for a password nobody can see
  $env:GIT_TERMINAL_PROMPT = '0'
  $code = Invoke-Native $Git @('-c', 'credential.helper=', 'clone', $env:HAL_REPO, $App)
  Remove-Item Env:\GIT_TERMINAL_PROMPT
  if ($code -ne 0 -and $script:LastOut -notmatch '403|404|not found|denied|authentication|could not read (username|password)') {
    Stop-Install 'Hal could not reach GitHub. Check the internet connection, then double-click this file again.'
  }
  $SignedIn = $false
  if ($code -ne 0) {
    Say 'GitHub wants you to sign in before Hal can get its files. Your browser will open.'
    $code = Invoke-Native $Git @('-c', 'credential.helper=manager', '-c', 'credential.https://github.com.provider=github', 'clone', $env:HAL_REPO, $App)
    $SignedIn = ($code -eq 0)
  }
  if ($code -ne 0) {
    # Git Credential Manager did not sign in: fall back to the GitHub CLI's code
    $GhDir = Join-Path $Tools 'gh'
    $Gh = Join-Path $GhDir 'bin\gh.exe'
    if (-not (Test-Path -LiteralPath $Gh)) {
      $zip = Join-Path $Tools 'gh.zip'
      Get-Pinned 'gh' $zip
      Expand-Archive -Force -LiteralPath $zip -DestinationPath $GhDir
      Remove-Item -Force -LiteralPath $zip
    }
    Say 'Paste the code shown here into the GitHub page; it is already copied.'
    & $Gh auth login --web --hostname github.com --git-protocol https --clipboard
    Invoke-Native $Gh @('auth', 'setup-git', '--hostname', 'github.com') | Out-Null
    if ((Invoke-Native $Git @('clone', $env:HAL_REPO, $App)) -ne 0) {
      Stop-Install "Hal could not sign you in to GitHub, or your account cannot read $env:HAL_REPO. Report it at $Issues, then double-click this file again."
    }
  } elseif ($SignedIn) {
    # the same sign-in for later updates, in the clone's own config; the person's global git config is untouched
    Invoke-Native $Git @('-C', $App, 'config', 'credential.helper', 'manager') | Out-Null
    Invoke-Native $Git @('-C', $App, 'config', 'credential.https://github.com.provider', 'github') | Out-Null
  }
}

# 4. Hal's own Python and packages ----------------------------------------------------------------------
Say-Step 'Setting up the Python and packages Hal uses (about 2 minutes)'
if ((Invoke-Native $Uv @('sync', '--frozen', '--no-dev', '--project', $App)) -ne 0) {
  Stop-Install "Hal could not set itself up. Double-click this file again. If it happens again, report it at $Issues with the file $Log"
}
$Py = Join-Path $App '.venv\Scripts\python.exe'
$Pyw = Join-Path $App '.venv\Scripts\pythonw.exe'

# 5. Shortcuts, the Startup entry and the install record --------------------------------------------------
Say-Step 'Making the shortcuts and the Startup entry'
if ((Invoke-Native $Py @('-X', 'utf8', '-m', 'jobhunt', 'install', '--windows')) -ne 0) {
  Stop-Install "Hal could not make its shortcuts. Double-click this file again. If it happens again, report it at $Issues with the file $Log"
}

# 6. An older Hal in the user's folder, copied only when one is found ---------------------------------
if ($HasOld) {
  Say-Step 'Copying your jobs, applications and settings from your older Hal'
  if ((Invoke-Native $Py @('-X', 'utf8', '-m', 'jobhunt', 'import', '--from-usb-install', $Old)) -eq 0) {
    Say 'Copied your jobs, applications and settings from your old Hal. Nothing was deleted.'
  } else {
    Say 'Hal could not copy your old Hal. It is still there, untouched; the setup guide offers to try again.'
  }
}

# 7. Start Hal ------------------------------------------------------------------------------------------
Say-Step 'Starting Hal'
if (-not (Test-HalAnswers $Py)) {
  Start-Process -FilePath $Pyw -ArgumentList '-X utf8 -m jobhunt serve --background' -WorkingDirectory $App
  $deadline = (Get-Date).AddSeconds($HealthWaitSeconds)
  while (-not (Test-HalAnswers $Py)) {
    if ((Get-Date) -gt $deadline) {
      Stop-Install "Hal did not start. Double-click this file again. If it happens again, report it at $Issues with the file $Log"
    }
    Start-Sleep -Seconds 1
  }
}

# 8. The setup guide -----------------------------------------------------------------------------------
Say-Step 'Opening the setup guide'
Start-Process -FilePath $Pyw -ArgumentList '-X utf8 -m jobhunt open --next /#setup' -WorkingDirectory $App
Say 'Hal Employment is installed. You can close this window.'
