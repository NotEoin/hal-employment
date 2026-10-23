# Load the browser helper

The browser helper lets Hal fill in job application forms in your own browser, where you are already signed
in to job sites. It only acts on tabs Hal opens or you hand to it, and it never presses Submit: you always
press Submit on the employer's site yourself.

The helper works in Opera, Google Chrome, Microsoft Edge, Brave and Vivaldi. It does not work in Firefox
yet. Setup's "Browser helper" step shows these steps with a picture of each one for the browser you choose.

1. In Hal's setup guide, open the **Browser helper** step and pick your browser.
2. Press the button that opens your browser's extensions page, or type its address yourself:

   ```text
   opera://extensions      # Opera
   chrome://extensions     # Google Chrome
   edge://extensions       # Microsoft Edge
   brave://extensions      # Brave
   vivaldi://extensions    # Vivaldi
   ```

3. Switch on **Developer mode**. In Opera, Chrome, Brave and Vivaldi the switch is at the top right of the
   page; in Edge it is in the column on the left. Leave it on: the browser turns the helper off without it.
4. Press **Load unpacked** and choose Hal's helper folder. Setup shows the folder with a **Copy** button:

   ```text
   Documents/Hal Employment/Browser helper       # on Linux
   %USERPROFILE%\Hal Employment\Browser helper   # on Windows
   ```

   The browser says the helper can "read and change all your data on all websites". That is needed because
   job forms are on thousands of different sites; the helper only acts on tabs Hal opened or you handed to it.
5. Back in setup, press **Connect**. Hal opens a small page in your browser that links the helper to your
   copy of Hal, so no other program can use it. The step ticks as soon as the helper is in touch.

Hal copies the helper into that folder every time it starts, so when Hal updates, the helper updates too: it
reloads itself the next time it talks to Hal.

On a laptop that uses Hal from another computer (see
[use-hal-from-another-computer.md](use-hal-from-another-computer.md)), the launcher copies the helper to
`Hal Employment/Browser helper` in the laptop's home folder. Hal cannot open the laptop's browser, so the
step shows the address to type and a link to open in that browser instead of the buttons.

| Symptom | Fix |
| --- | --- |
| "Waiting for the helper..." does not tick | Check the helper is switched on in the extensions page, then press **Connect** again. A link runs out after two minutes. |
| "This pairing code is not valid any more" | The link was used already or is over two minutes old. Press **Connect** again. |
| The helper disappeared after a restart of the browser | "Developer mode" was switched off. Switch it on again; the helper comes back. |
| The browser cannot see the helper folder | On Linux, a snap or flatpak browser cannot read hidden folders. The folder is in Documents for that reason; choose it from there. |
| "Is Hal working?" says "The browser helper is older than Hal" | Restart Hal so it refreshes the folder, then press reload on the helper in the extensions page. On a laptop, run the launcher again; it copies the new helper. |
| "Open Hal in the browser that has the helper" | Hal is filling a form through the helper, but that browser is closed. Open it, or open Hal from the browser where you loaded the helper. |
| Setup says it found an older Hal helper | Remove the old "Hal Employment" helper on the extensions page, so the two do not get in each other's way. |
| You use Firefox | The helper does not work in Firefox yet. Use one of the other browsers for applications. |
