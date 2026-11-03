# Use Hal from another computer

Hal and its AI keep running on the computer they are installed on (here called the desktop). Another
computer on the same home network (here called the laptop) opens the full app through an SSH connection:
your jobs, documents, record, settings and the desktop's AI, with forms filled in the laptop's own browser.
Hal still listens only on the desktop itself (127.0.0.1); the SSH server is the only thing the network can
reach. Reaching the desktop from outside your home needs a VPN, which Hal does not set up.

This page is for people comfortable with a terminal. Settings, **Use Hal from another computer** shows the
same steps with this computer's name, your user name and Copy buttons. In the commands below, replace
`USER@DESKTOP` with what Settings shows.

## Once, on the desktop (Linux)

1. Install and switch on the SSH server. Both commands ask for your password.

   ```sh
   sudo apt install openssh-server        # the SSH server
   sudo systemctl enable --now ssh        # start it now and at every start-up
   ```

2. Keep the desktop awake while you are away from it: Settings, Power, **Automatic suspend: Off** when
   plugged in. A desktop that sleeps cannot answer the laptop.

## Once, on the laptop

3. Make a key and copy it to the desktop. Press Enter at each question of `ssh-keygen`; on Windows a key
   without a passphrase saves typing it three times per start (or switch on the "OpenSSH Authentication
   Agent" service, which needs an administrator once).

   ```sh
   ssh-keygen -t ed25519                  # Linux, Mac or Windows
   ssh-copy-id USER@DESKTOP               # Linux or Mac: asks for the desktop's password once
   ```

   On Windows, in PowerShell, instead of `ssh-copy-id`:

   ```powershell
   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh USER@DESKTOP "cat >> ~/.ssh/authorized_keys"   # add the key
   ```

4. Back on the desktop, switch off password sign-in, so only your key gets in:

   ```sh
   echo "PasswordAuthentication no" | sudo tee /etc/ssh/sshd_config.d/hal-employment.conf   # keys only
   sudo systemctl reload ssh                                                                  # apply it
   ```

5. Get the launcher onto the laptop and add it to the menu. Linux or Mac:

   ```sh
   ssh USER@DESKTOP '~/.local/bin/hal-employment-cli remote-launcher --os linux' > ~/hal-remote.sh   # fetch it
   chmod +x ~/hal-remote.sh                                                                          # make it runnable
   ~/hal-remote.sh --install                                                                         # add it to the menu
   ```

   Windows, in Command Prompt (not PowerShell, which would change the file's bytes):

   ```bat
   ssh USER@DESKTOP "~/.local/bin/hal-employment-cli remote-launcher --os windows" > %USERPROFILE%\hal-remote.cmd   & rem fetch it
   %USERPROFILE%\hal-remote.cmd --install   & rem adds "Hal Employment (desktop)" to the Start menu
   ```

   Settings on the desktop also offers both launchers as downloads. If the desktop has no
   `~/.local/bin/hal-employment-cli` yet, the steps in Settings call Hal's own Python instead.
   A desktop that runs Hal Employment from a git checkout, or through Hal, has no such command until
   you make it once, in that checkout on the desktop:

   ```sh
   .venv/bin/python -m jobhunt install --dev   # makes ~/.local/bin/hal-employment-cli and a menu entry
   ```

## Every time

6. Open **Hal Employment (desktop)** from the laptop's menu. A small window opens and holds the connection;
   Hal opens in its own window. Close the small window when you have finished.
7. The first time, load the browser helper in the laptop's browser from the folder
   `Hal Employment/Browser helper` in your home folder (the launcher copies it, and refreshes it whenever the
   desktop's Hal is updated). Press **Connect** in the helper step and open the link it shows in that browser.

## What changes in a window from another computer

- **Open the folder** is replaced by downloads: **Download all** in Documents, **Download** for each record
  file. Files you add (CV, record files) are uploaded from the laptop.
- Forms are filled in the laptop's browser by its helper; **Show me the form** brings that tab forward.
- Hal's alerts show in the laptop's window (press **Allow alerts** once in Settings), and the desktop's
  pop-ups are held back while that window is open.
- Claude and GitHub sign-ins, start at login, the port and the data folder are changed on the desktop.
- Settings lists the windows open from other computers, each with **Sign out**.

| Symptom | Fix |
| --- | --- |
| "Hal is not answering on ... Is the desktop on and awake?" | Wake the desktop, check step 2's power setting, and check you are on the home network. |
| `ssh: connect to host ... port 22: Connection refused` | The SSH server is not running on the desktop. Run step 1 again; Settings shows "Installed and running" when it is. |
| `Permission denied (publickey)` | The key was not copied, or password sign-in was switched off first. Copy the key (step 3) before step 4. |
| "For your safety, Hal opens from its shortcut." | The window's link was used or ran out. Close the small window and open the launcher again. |
| "Could not copy the browser helper" | The connection dropped while copying. Open the launcher again; Hal itself still opens. |
| The browser helper says it is older than Hal | Open the launcher again: it copies the desktop's new helper, then reload the helper in the browser. |
| Windows asks for the key's passphrase three times | Use a key without a passphrase on this laptop, or switch on the "OpenSSH Authentication Agent" service. |
| `bash: ~/.local/bin/hal-employment-cli: No such file or directory` when fetching the launcher | The desktop runs Hal Employment from a checkout or through Hal. Run the `install --dev` line in step 5 on the desktop. |
| The small window closes at once | Fetch the launcher again (step 5): launchers made before 2026-11-03 closed as soon as the tunnel started. A launcher that fails now stays open and says why. |
| Hal opens on another port number | Something on the laptop already uses that port; the launcher picked the next free one. Nothing to fix. |
