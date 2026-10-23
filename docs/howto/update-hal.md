# Update Hal Employment

Hal looks for a new version every morning at 09:00 (Settings, Updates, "Check for updates every day") and
tells you on Home and in Messages when one is ready. It installs only when you press **Update Hal**, and
never while it is filling in a form.

1. Open **Settings**, **Updates**. "Version" says which version you have and whether a new one is ready.
2. Read **What's new**, then press **Update Hal**.
3. Hal stops for about a minute, updates, and starts again by itself. The page comes back on its own and says
   how it went, for example "Updated Hal from 2.0.1 to 2.0.2".
4. If something is wrong after an update, press **Go back to the previous version**. Changes you made since
   the update, such as new applications, are lost.

If the app does not start at all, update from outside it:

- Windows: Start menu, **Update Hal Employment**. A window shows the steps; press any key to close it.
- Linux: run this in a terminal:

  ```bash
  ~/.local/opt/hal-employment/app/scripts/update-hal.sh   # the same update, shown step by step
  ```

| Symptom | Fix |
| --- | --- |
| "Hal is filling in a form. It will update when it has finished." | Nothing: the update starts by itself when the form is done. |
| "Hal cannot reach its updates on GitHub. Sign in to GitHub again." | Press **Sign in to GitHub again** in Settings, Updates, on the computer Hal runs on. |
| "Your GitHub account no longer has access to Hal's files" | Ask the person who set Hal up to add you again. |
| "Hal's files were changed by hand, so it cannot update by itself" | Ask the person who set Hal up; they can put the files back. |
| "This update was not made by the person who set Hal up, so Hal did not install it" | Do not install it. Tell the person who set Hal up. |
| "The update did not work, so Hal went back to the version you had" | Nothing was lost. Hal will not offer that version again; the next one is offered as usual. Details shows what failed. |
| "You run Hal from your own folder. Update it with git." | This is a developer's checkout: `git pull --ff-only`, then `uv sync --frozen`, then `python -m jobhunt migrate`. |
