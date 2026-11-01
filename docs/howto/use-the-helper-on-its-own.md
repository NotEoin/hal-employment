# Use the helper on its own

Without Hal, Hal Employment uses the AI you choose in the setup guide. Hal's gateway is only used when you
choose it, so the helper works the same whether or not Hal is installed.

1. Open the setup guide (or **Settings**, **How Hal thinks**) and pick one of the choices below.
2. For Claude, sign in once. Hal Employment runs Claude Code on your own subscription.

   ```bash
   claude          # follow the sign-in; Hal Employment then shows "signed in"
   ```

3. For a model on this computer, install Ollama and pull a model. Hal Employment finds it by itself.

   ```bash
   ollama pull llama3.2     # any chat model; Settings lists what is installed
   ```

4. Open **Settings**. The line **AI mode** says which one is in force.

| Choice | AI mode line | Where your data goes |
| --- | --- | --- |
| Claude subscription | On its own: your Claude subscription (Claude Code) | The advert and your CV go to Claude |
| This computer | On its own: the AI on this computer | Nothing leaves the computer |
| Another local server | On its own: another local server | To the address you set |

On Linux the helper suggests This computer when Ollama answers and Claude otherwise. It suggests Hal's local
model only when Hal's gateway answers. The suggestion is made once; it never changes by itself.

| Symptom | Fix |
| --- | --- |
| "Claude is not signed in" | Run `claude` and sign in, then press **Check again** in Settings. |
| Jobs are not scored and AI mode says "the AI on this computer" | Start Ollama, then press **Check again**. |
| You installed Hal later and want its model | Follow [Use the helper with Hal](use-the-helper-with-hal.md). |
