@echo off
rem Opens Hal Employment, running on the desktop, through an SSH tunnel.
rem Made by "hal-employment-cli remote-launcher --os windows" on the desktop, which fills in HOST and PORT.
rem Run it once with --install to add "Hal Employment (desktop)" to the Start menu. Windows' OpenSSH has
rem no control connections, so each run makes three SSH logins: use a key without a passphrase on this
rem computer, or switch on the "OpenSSH Authentication Agent" service. ssh.exe is called unquoted because
rem %SystemRoot% holds no spaces and for /f strips quotes; %URL% is always quoted because a link may hold an ampersand.
setlocal
set "HOST=USER@DESKTOP"
set "PORT=8737"
set "LPORT=8737"
set "HAL=~/.local/bin/hal-employment-cli"
set "SSH=%SystemRoot%\System32\OpenSSH\ssh.exe"
set "OPTS=-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15 -o ServerAliveCountMax=3"
set "HELPER=%USERPROFILE%\Hal Employment\Browser helper"
if /i "%~1"=="--install" goto install
if not exist "%HELPER%" mkdir "%HELPER%"
taskkill /fi "WINDOWTITLE eq Hal tunnel*" >nul 2>&1
:port
netstat -an | findstr /c:"127.0.0.1:%LPORT% " >nul && (set /a LPORT+=1 & goto port)
%SSH% %OPTS% %HOST% "%HAL% helper-files --tar" | tar -x -C "%HELPER%" || echo Could not copy the browser helper
start "Hal tunnel" /min %SSH% %OPTS% -o ExitOnForwardFailure=yes -N -L 127.0.0.1:%LPORT%:127.0.0.1:%PORT% %HOST%
set "URL="
for /f "usebackq delims=" %%u in (`%SSH% %OPTS% %HOST% "%HAL% open-url --remote --local-port %LPORT%"`) do set "URL=%%u"
if not defined URL echo Hal is not answering on %HOST%. Is the desktop on and awake?& goto end
set /a TRIES=0
:wait
curl.exe -fs "http://127.0.0.1:%LPORT%/healthz" >nul 2>&1 && goto open
set /a TRIES+=1
if %TRIES% lss 20 timeout /t 1 /nobreak >nul& goto wait
:open
rem msedge is not on PATH: App Paths work only through start
start "" msedge --app="%URL%"
echo Hal is open. Close this window when you have finished.
pause
:end
taskkill /fi "WINDOWTITLE eq Hal tunnel*" >nul 2>&1
exit /b

:install
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs') + '\Hal Employment (desktop).lnk'); $s.TargetPath = '%~f0'; $s.Save()"
echo Added "Hal Employment (desktop)" to the Start menu.
exit /b
