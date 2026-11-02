# Use the helper with Hal

With Hal's gateway running, Hal Employment uses Hal's local model for every AI job. It never calls Claude
in this mode, even when the gateway is down: you get a plain-words message instead. Hal Employment takes
the gateway's address and token from two settings, so nothing else is needed.

1. Turn the gateway's loopback address on in Hal, then restart the gateway.

   ```toml
   [gateway]
   tcp = true        # off by default; the address is then 127.0.0.1:8741
   ```

2. Check that the gateway answers, then mint a client token for Hal Employment. Hal prints it once.
   Save it as one line in `~/.config/hal/gateway-token`, which is where Hal Employment looks first.

   ```bash
   hal-gateway status                              # the gateway is up
   hal-gateway token issue --name hal-employment   # copy the secret now
   umask 077 && printf '%s\n' '<the secret>' > ~/.config/hal/gateway-token   # mode 0600, one line
   ```

3. Choose Hal's model. Either open **Settings**, **How Hal thinks**, **Hal's local model**, or run one
   command. The setup guide offers "Use Hal's own AI" by itself when it finds the gateway.

   ```bash
   python -m jobhunt settings set ai.provider hal   # the same choice as in Settings
   ```

4. Open **Settings**. The line **AI mode** says "With Hal: the local model, through Hal's gateway".
5. Optional: if the gateway or token are somewhere else, change them under **Settings**, **Advanced**.
   The address must be on this computer (`127.0.0.1` or `localhost`); for a gateway on another computer,
   use an SSH tunnel to a local port. Hal never sends the token anywhere else. Both settings can only be
   changed on this computer, not from a paired laptop.

   ```bash
   python -m jobhunt settings set ai.hal_url http://127.0.0.1:8741/v1   # the gateway address
   python -m jobhunt settings set ai.hal_token_file ~/.config/hal/gateway-token   # the token file
   ```

6. Optional: let Hal's jobs pack read your queue. Point it at this checkout in Hal's config file.

   ```toml
   [jobs]
   jobhunt_root = "/path/to/employment-helper"   # the folder that holds the jobhunt package
   ```

The gateway claims the graphics card itself, through gpu-light when that is installed, so Hal Employment
does not claim it a second time. If the gateway also fronts an online model (`allow_remote = true`),
Hal Employment does not list that model and refuses to use it, so your data stays on this computer. To let Hal Employment fall back to Claude, pick **Claude subscription**
yourself; it never switches by itself.

| Symptom | Fix |
| --- | --- |
| "Hal's gateway does not answer at ..." | Set `tcp = true` under `[gateway]`, restart the gateway, then run `hal-gateway status`. |
| "Hal's gateway must be on this computer" | Set `ai.hal_url` to a `127.0.0.1` address, through an SSH tunnel if the gateway runs elsewhere. |
| "Hal's gateway token was not found" | Run `hal-gateway token issue --name hal-employment` and save the secret as one line in the token file. |
| "can be read by other users" on the AI status | The token file is open to group or others. Run `chmod 600 ~/.config/hal/gateway-token`; Hal keeps working meanwhile. |
| "Hal's gateway did not accept the token" | The token was rotated or revoked. Save the new one in the token file; no restart is needed. |
| "... is an online model behind Hal's gateway" | Choose a local model under **Settings**, **Hal's brain**, or clear the model setting so Hal picks one. |
| "jobhunt_root is not set" from a Hal jobs tool | Add `jobhunt_root` under `[jobs]` as in step 6. |
| AI mode still says "On its own" | Pick **Hal's local model** in Settings, or run the `settings set ai.provider hal` command above. |
