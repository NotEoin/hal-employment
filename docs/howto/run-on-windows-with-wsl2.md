# Run Hal Employment in WSL2 on Windows

This page shows how to run the Linux install of Hal Employment inside WSL2 on a Windows 11 PC, and
which parts of Hal work there. Most people on Windows should use the normal Windows install
([steps](install-on-windows.md)): it needs no WSL. Use WSL2 when the rest of the Hal suite runs in
WSL2 and Hal's local model (the gateway) is on the Linux side, so this Hal can reach it on `127.0.0.1`.

The WSL2 install is a separate Hal. Its data is in the distro's own `~/.config/hal-employment`, not in
your Windows user folder, and the two installs never share jobs, answers or settings.

Support is new and has not been run on a Windows PC yet. The table after the steps marks every feature
as verified or unverified.

## Steps

1. Enable WSL and install Ubuntu. In an administrator PowerShell on Windows:

   ```powershell
   wsl --install -d Ubuntu-24.04   # installs WSL2 and the distro; reboot when asked
   wsl --update                    # WSLg (windows and sound) ships with WSL
   wsl --status                    # Default Version: 2
   ```

2. Enable systemd inside the distro, so Hal can start when the distro starts. Inside Ubuntu:

   ```bash
   printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf   # edit by hand if [boot] exists
   ```

   ```powershell
   wsl --shutdown   # run in PowerShell; stops every WSL distro, then reopen Ubuntu
   ```

   ```bash
   systemctl --user is-system-running   # running or degraded, not "offline"
   ```

3. Install the tools the Linux install needs, and a terminal for the Claude sign-in window:

   ```bash
   sudo apt-get update && sudo apt-get install -y git curl xterm   # xterm shows as a window through WSLg
   ```

4. Install Hal inside Ubuntu, as on Linux ([steps](install-on-linux.md)):

   ```bash
   bash ~/Downloads/install-hal-linux.sh   # about 5 minutes; safe to run again
   ```

5. Open Hal. It opens in the distro's default browser through WSLg, if one is installed.
   Otherwise open the one-time link it prints in a Windows browser; Windows reaches WSL2 on `localhost`.

   ```bash
   ~/.local/bin/hal-employment-cli list -n 10   # the ten best open jobs; proves the install works
   ```

6. Optional: an NVIDIA GPU for Hal's local model. Install the driver on Windows, not in Ubuntu.

   ```bash
   nvidia-smi   # lists your GPU; if not, update the Windows driver
   ```

## What works under WSL2

"Unverified" means the design expects it to work, but nobody has run it on WSL2. Run the check in the
last column and report the result.

| Feature | Status | How to check |
| --- | --- | --- |
| Platform check (`osrelease` says `microsoft` or `WSL`) | Verified with a fake `osrelease` in the test suite | `python -m unittest discover tests -p "test_wsl2.py"` |
| Start at login says to turn systemd on, instead of claiming it is enabled | Verified with a fake `osrelease` and a fake systemd folder | Settings, "Start when you sign in", before step 2 |
| The Claude sign-in names `xterm` when no terminal exists | Verified with a fake `PATH` | Choose Claude sign-in in Settings before step 3 |
| Local-model wait ignores unnamed `nvidia-smi` rows | Verified with a fake `[N/A]` row; unverified that WSL2 prints one | `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits` |
| Hermetic test suite | Wired into CI on a Windows runner; not yet seen green | The `wsl` job of the CI workflow |
| Jobs, scoring, CV, answers, the app on `127.0.0.1` | Unverified on WSL2; they are the Linux code paths | `hal-employment-cli list -n 10` |
| Passwords and keys | Unverified; a fresh distro has no desktop keyring, so Hal falls back to `config/secrets.json` with mode 0600 | Settings shows where the password is kept |
| App window through WSLg | Unverified; needs Chromium, Chrome, Brave or Opera in the distro | Open Hal from the menu |
| Opening a link in a Windows browser | Unverified; needs `wslu` (`wslview`) for `xdg-open` | `xdg-open https://example.org` |
| The browser helper in a Windows browser | Unverified; it must reach Hal over `localhost` | The helper's connect step ([steps](load-the-browser-helper.md)) |
| Desktop pop-ups (`notify-send`) | Unverified; WSLg runs no notification daemon, so Hal shows none | `notify-send test` |
| Playwright browser tests and form filling | Unverified; need the Chromium libraries in the distro | `uv run python -m playwright install --with-deps chromium` |
| Start at login through the systemd user unit | Unverified; needs systemd in `/etc/wsl.conf` | `systemctl --user status hal-employment-app` |

## Symptoms and fixes

| Symptom | Fix |
| --- | --- |
| Start at login says to turn systemd on under WSL2 | Do step 2. |
| The Claude sign-in says no terminal program was found | Run `sudo apt-get install -y xterm`. |
| `systemctl --user` says `Failed to connect to bus` | Do step 2, then `wsl --shutdown`. |
| Hal stops when you close the last Ubuntu window | WSL may stop an idle distro (unverified). Keep a terminal open. |
| Hal opens no window | Install a Chromium-family browser in Ubuntu, or open the printed link in a Windows browser. |
| Links do not open from Hal | Install `wslu` (`sudo apt-get install -y wslu`) so `xdg-open` reaches Windows (unverified). |
| Hal waits for the GPU for ever | A Linux program holds it. Add its name to the GPU ignore list in Settings. |
| `nvidia-smi` is not found in Ubuntu | Install or update the NVIDIA driver on Windows, then `wsl --shutdown`. Do not install a Linux driver in the distro. |
| Your jobs from the Windows install are missing | The WSL2 Hal has its own data. Use the importer in Settings to bring the old data across. |
| The browser helper cannot find Hal | Open Hal's link in the same Windows browser, and check that Windows can open `http://127.0.0.1:PORT` with the port Hal printed. |
