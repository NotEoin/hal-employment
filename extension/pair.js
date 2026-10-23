// Runs on pages of 127.0.0.1 and localhost, and acts only on Hal's own pairing page: a page at /pair that
// carries <meta name="hal-app" content="<install id>">. It passes the one-time code in the address's
// fragment (which never reaches a server log) to the helper's background, which swaps it for a key,
// then says on the page whether that worked.

(() => {
  if (location.pathname !== "/pair") return;
  const app = document.querySelector('meta[name="hal-app"]');
  if (!app || !app.content) return;
  const code = new URLSearchParams(location.hash.slice(1)).get("code");
  if (!code) return;
  const say = (text, ok) => {
    let line = document.getElementById("hal-pair-result");
    if (!line) {
      line = document.createElement("p");
      line.id = "hal-pair-result";
      line.setAttribute("role", "status");
      document.body.appendChild(line);
    }
    line.textContent = text;
    line.dataset.ok = ok ? "yes" : "no";
  };
  say("Connecting...", false);
  chrome.runtime.sendMessage({ halPair: { code, install_id: app.content } }, reply => {
    if (chrome.runtime.lastError || !reply) say("The helper could not reach Hal. Press Connect in Hal again.", false);
    else if (reply.ok) say("Connected. Hal can now fill forms in this browser. You can close this tab.", true);
    else say(reply.error, false);
  });
})();
