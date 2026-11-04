// Runs inside a job tab Hal opened: reports what the page shows, presses Apply
// and Next controls, and types approved answers into the form. It never
// presses a submit button and never presses Enter: every press asks
// `neverPress`, the guard of jobhunt/apply/guard.py, whose patterns arrive with
// each command from jobhunt/extension.py so the rules live in one place.

(() => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const rx = source => new RegExp(source, 'i');
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const nameOf = el => (el.getAttribute('aria-label') || el.innerText || el.value || '').replace(/\s+/g, ' ').trim();
  // halAll (page.js) also looks inside page components, whose controls sit in open shadow roots.
  const controls = (selector, pattern) => halAll(selector).filter(el => visible(el) && rx(pattern).test(nameOf(el)));
  const BUTTONS = "button, [role=button], input[type=submit], input[type=button]";
  const POPUP_GONE_MS = 400;  // a closed pop-up fades out before the page under it takes clicks
  // Widgets such as react-select open on mouse-down, which a bare click() never sends.
  const tap = el => ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(type =>
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window })));
  const halAround = "[role=dialog], [aria-modal=true], dialog, form";
  const tagged = hid => halAll(`[data-hal-id="${CSS.escape(hid)}"]`)[0] || null;

  // Cookie banners block clicks underneath them; refusing optional cookies is preferred. A button inside a
  // form is the form's own: "I agree" there is consent the person gives, never a banner to close.
  // `never` holds the words a site note says never to press there.
  async function dismissBanners(patterns, never) {
    for (const pattern of patterns) {
      const button = controls(BUTTONS, pattern).find(el => !el.closest('form') && !(never && rx(never).test(nameOf(el))));
      if (button) { button.click(); await sleep(500); return; }
    }
  }

  // Dropdowns: opened, read through page.js's halOptions, and closed again without disturbing the form.
  function openList(el) {
    el.focus();
    tap(el);
    if (!halOptions(el.getAttribute('data-hal-id')).length)
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', code: 'ArrowDown', bubbles: true }));
  }
  // Escape also closes the dialog a form may sit in (Workable's did, 2026-10-20), so a list inside a form or
  // dialog is closed by a press on that, which is outside the list but still inside the form.
  function closeList(el) {
    el.blur();
    if (!document.querySelector('[data-hal-opt]')) return;
    const around = el.closest(halAround);
    if (around) tap(around);
    else el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
  }
  // A list fetched from the site as it opens is given time to arrive.
  async function listed(hid, waits = 3) {
    let options = [];
    for (let i = 0; i < waits && !options.length; i++) { await sleep(300); options = halOptions(hid); }
    return options;
  }
  async function comboboxOptions(el) {
    const hid = el.getAttribute('data-hal-id');
    halStale();
    openList(el);
    let options = await listed(hid);
    if (!options.length) {
      // The first press can be spent closing the list of the control before.
      openList(el);
      options = await listed(hid, 2);
    }
    closeList(el);
    return options;
  }

  async function read(p) {
    if (p.banners) {
      await dismissBanners(p.banners, p.bannersNever);
      if (halPopup()) await sleep(POPUP_GONE_MS);
    }
    const fields = halExtract();
    if (p.options) {
      // Only a list still empty is opened: one already answered needs no options, and each opening is slow.
      for (const f of fields) {
        if (f.kind === 'combobox' && !f.current && !(f.options || []).length) f.options = await comboboxOptions(tagged(f.hid));
      }
    }
    const frame = [...document.querySelectorAll('iframe[src]')].find(f => p.frames.some(host => f.src.includes(host)));
    const turnstile = [...document.querySelectorAll("input[name='cf-turnstile-response']")];
    const survey = halSurvey();
    return {
      url: location.href, title: document.title, fields, controls: survey.controls, alerts: survey.alerts,
      text: (document.body.innerText || '').slice(0, 40000),
      headings: [...document.querySelectorAll('h1, h2')].slice(0, 4).map(h => h.innerText).join(' '),
      password: [...document.querySelectorAll('input[type=password]')].some(visible),
      apply: controls("a[href], [role=link], " + BUTTONS, p.apply).length > 0,
      next: controls(BUTTONS, p.next).length > 0,
      submit: controls(BUTTONS, p.submit).length > 0,
      signOut: controls("a, button", p.signOut).length > 0,
      frame: frame ? frame.src : '',
      captcha: document.querySelector(p.captcha) !== null,
      unchecked: turnstile.some(e => !e.value),
      stayed: [...document.querySelectorAll('[data-hal-step]')].some(visible),
      invalid: [...document.querySelectorAll('[aria-invalid=true]')].filter(visible).length,
    };
  }

  // The reason a control must not be pressed, or null: the rules of `never_press` in jobhunt/apply/guard.py.
  // A <button> with no type inside a form sends it under HTML, so it counts as submit-like unless its words
  // are the site's Next words; when in doubt the page is final.
  function neverPress(el, p) {
    const g = p.guard, page = p.page || {};
    const texts = [el.getAttribute('aria-label') || '', el.innerText || '', el.value || '']
      .map(t => String(t).replace(/\s+/g, ' ').trim());
    const name = texts.find(Boolean) || '';
    const said = name ? `"${name.slice(0, 60)}"` : 'an unlabelled button';
    const nexty = texts.some(t => t && rx(g.nextWords).test(t));
    const final = page.final !== false;
    if (texts.some(t => t && rx(g.always).test(t))) return `${said} would send the application`;
    if (texts.some(t => t && rx(g.final).test(t)) && (p.kind !== 'apply' || page.form || page.signed_in))
      return `${said} would send the application`;
    const submitter = /^(BUTTON|INPUT)$/.test(el.tagName) && /^(submit|image)$/.test(el.type);
    if (submitter && el.closest('form') && !(nexty && !final)) return `${said} sends the form it is in`;
    if (p.kind === 'next' && !nexty) return `${said} is not this form's Next button`;
    if (p.kind === 'named' && page.form && final && !nexty) return `${said} is on the form's last page`;
    return null;
  }

  function press(p) {
    let control;
    if (p.kind === 'named') {
      // A control the helper chose by name, on a page with no Apply button it recognised.
      control = [...document.querySelectorAll("a[href], [role=link], " + BUTTONS)].find(el => visible(el) && nameOf(el) === p.name);
    } else {
      const found = p.kind === 'apply' ? controls("a[href], [role=link], " + BUTTONS, p.pattern) : controls(BUTTONS, p.pattern);
      control = p.kind === 'apply' ? found[0] : found[found.length - 1];
    }
    if (!control) return { pressed: false };
    const refused = neverPress(control, p);
    if (refused) return { pressed: false, refused };
    if (p.kind === 'next') {
      // Marks this step's controls so a form that refuses to move on can be told from a new step.
      document.querySelectorAll('[data-hal-id]').forEach(e => e.setAttribute('data-hal-step', ''));
    }
    // The page-world block lets through a submit only from the control marked here.
    control.setAttribute('data-hal-allow', '');
    try { control.click(); } finally { control.removeAttribute('data-hal-allow'); }
    return { pressed: true };
  }

  // Types an emailed code into the boxes the helper named, and presses nothing: the person presses the button.
  function typeCode(p) {
    halExtract();
    const boxes = (p.boxes || []).map(tagged).filter(Boolean);
    const code = String(p.code).replace(/\s+/g, '');
    if (boxes.length === 1) typeText(boxes[0], code, false);
    else boxes.forEach((el, i) => { if (code[i] !== undefined) typeText(el, code[i], false); });
    return { typed: boxes.length };
  }

  // Frameworks keep their own copy of a value; the native setter and an input event update both.
  function setValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // Text goes in through the browser's own editing command, as typing or pasting would: the
  // page sees ordinary input events, rich-text editors update their model, and Ctrl+Z works.
  function typeText(el, text, custom) {
    // A line break typed into a one-line box is Enter, which sends the form.
    if (!custom && !(el instanceof HTMLTextAreaElement)) text = String(text).replace(/[\r\n]+/g, ' ');
    el.focus();
    if (custom) document.getSelection().selectAllChildren(el);
    else try { el.select(); } catch { /* a box that cannot be selected is replaced below */ }
    const typed = document.execCommand('insertText', false, text);
    const now = custom ? el.innerText : el.value;
    if (!typed || norm(now) !== norm(text)) {
      if (custom) el.innerText = text;
      else setValue(el, text);
    }
  }

  const norm = t => String(t ?? '').replace(/\s+/g, ' ').trim().toLowerCase();
  function bestOption(options, value) {
    const v = norm(value);
    for (const test of [o => norm(o) === v, o => norm(o).startsWith(v), o => norm(o).includes(v)]) {
      const i = options.findIndex(test);
      if (i >= 0) return i;
    }
    return -1;
  }

  // A date box takes yyyy-mm-dd, whatever way the answer writes the date.
  function isoDate(value) {
    const m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/.exec(String(value).trim());
    if (m) return `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`;
    const d = new Date(value);
    return isNaN(d) ? String(value) : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  const isOn = el => el.checked === true || el.getAttribute('aria-checked') === 'true'
    || el.getAttribute('aria-pressed') === 'true' || /(^|[\s_-])(active|selected|checked)/i.test(el.className);
  // Styled radios and tick boxes hide the control; its label takes the click instead.
  function setChecked(el, want, p) {
    if (isOn(el) === want) return;
    // A styled choice is often a button; one whose words could send the form is never pressed.
    if (!/^(radio|checkbox)$/.test(el.type) && rx(p.guard.always).test(nameOf(el)))
      throw new Error(`Hal stopped before pressing Submit: "${nameOf(el).slice(0, 60)}" would send the application`);
    el.click();
    if (isOn(el) !== want && el.labels && el.labels[0]) el.labels[0].click();
  }

  // Chooses `value` in a dropdown: opens it, types to narrow a long or searched list, presses the option.
  async function pick(el, f, value) {
    value = String(value);
    halStale();
    openList(el);
    let options = await listed(f.hid);
    if (!options.length) { openList(el); options = await listed(f.hid, 2); }
    let index = bestOption(options, value);
    if (index < 0 && f.typeable) {
      // "London, United Kingdom" may be wanted as a city or as a country.
      const parts = value.split(',').map(x => x.trim()).filter(Boolean);
      for (const query of new Set([value, ...parts, value.split(/\s+/)[0].replace(/,$/, '')])) {
        typeText(el, query, false);
        for (let i = 0; i < 8 && index < 0; i++) {
          await sleep(400);
          options = halOptions(f.hid);
          index = bestOption(options, value);
          if (index < 0 && options.length && query !== value) index = bestOption(options, query);
        }
        if (index >= 0) break;
      }
    }
    const option = index >= 0 && halAll(`[data-hal-opt="${index}"]`)[0];
    if (!option) {
      closeList(el);
      throw new Error(`no option matching "${value}"` + (options.length ? `; it offers ${options.slice(0, 6).join(', ')}` : ''));
    }
    tap(option);
    await sleep(250);
  }

  async function fillOne(f, files, p) {
    const value = f.value;
    if (f.kind === 'radio' || f.kind === 'checkgroup') {
      const wanted = (Array.isArray(value) ? value : [value]).map(norm);
      const missing = wanted.find(w => !f.options.some(o => norm(o) === w));
      if (missing !== undefined) throw new Error(`no option "${missing}"`);
      const first = tagged(`${f.hid}:0`);
      if (first) first.scrollIntoView({ block: 'center' });
      f.options.forEach((option, i) => {
        const el = tagged(`${f.hid}:${i}`);
        const chosen = wanted.includes(norm(option));
        if (el && (chosen || f.kind === 'checkgroup')) setChecked(el, chosen, p);
      });
      return;
    }
    const el = tagged(f.hid);
    if (!el) throw new Error('the field is no longer on the page');
    if (f.kind === 'file') {
      const file = files[value];
      const bytes = Uint8Array.from(atob(file.data), c => c.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], file.name, { type: file.type }));
      el.files = transfer.files;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      // The site reads the file before the form is ready to go on.
      await sleep(1000);
      return;
    }
    el.scrollIntoView({ block: 'center' });
    if (f.kind === 'checkbox') setChecked(el, Boolean(value), p);
    else if (f.kind === 'select') {
      const options = [...el.options];
      // A list that takes several answers gets every one of them.
      const wanted = Array.isArray(value) ? value : [value];
      const picked = wanted.map(w => {
        const index = bestOption(options.map(o => o.text), w);
        if (index < 0) throw new Error(`no option "${w}"`);
        return options[index];
      });
      el.focus();
      if (el.multiple) {
        options.forEach(o => { o.selected = picked.includes(o); });
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      } else setValue(el, picked[0].value);
    } else if (f.kind === 'combobox') {
      await pick(el, f, value);
    } else {
      let text = el.type === 'date' ? isoDate(value) : String(value);
      if (el.maxLength > 0) text = text.slice(0, el.maxLength);
      if (el.type === 'date') { el.focus(); setValue(el, text); }
      else typeText(el, text, el.isContentEditable);
    }
    // Many forms check and keep an answer only when the box is left.
    el.blur();
  }

  // True when the page shows the file it was given: in the input, or by name where the site lists it. Upload
  // widgets often empty their input once they take the file; one that drops it silently shows neither.
  function uploaded(f, file) {
    const name = String(file.name).toLowerCase();
    const el = tagged(f.hid);
    const inInput = el && el.files && [...el.files].some(x => x.name.toLowerCase() === name);
    return Boolean(inInput) || halText().toLowerCase().includes(name);
  }

  async function fill(p) {
    const errors = {}, notTaken = [];
    for (const f of p.fields) {
      try { await fillOne(f, p.files, p); } catch (e) { errors[f.hid] = String(e.message || e).slice(0, 200); }
      // One answer at a time, at about the pace of a quick paste, so each is taken before the next.
      await sleep(120 + Math.random() * 200);
    }
    for (const f of p.fields) {
      if (f.kind === 'file' && !errors[f.hid] && p.files[f.value] && !uploaded(f, p.files[f.value])) notTaken.push(f.hid);
    }
    return { errors, notTaken };
  }

  // Marking up a filled page --------------------------------------------------

  const MARKS = `
    [data-hal-mark] { outline-offset: 3px !important; border-radius: 2px; }
    [data-hal-mark=ok] { outline: 2px solid rgba(22, 163, 74, .55) !important; }
    [data-hal-mark=check] { outline: 2px solid #f59e0b !important; }
    [data-hal-mark=answer], [data-hal-mark=error] { outline: 2px solid #dc2626 !important; }
    [data-hal-mark=edited] { outline: 2px solid rgba(37, 99, 235, .6) !important; }`;

  // The box to outline: the control itself, or for hidden and tiny controls the nearest box around them that shows.
  function shown(el) {
    if (!el.checkVisibility()) {
      // A styled tick box hides its input and shows its label; anything else hidden is on a step not on show.
      const label = el.labels && el.labels[0];
      if (!label || !label.checkVisibility()) return null;
      el = label;
    }
    for (let node = el, depth = 0; node && depth < 5; node = node.parentElement, depth++) {
      const r = node.getBoundingClientRect();
      if (r.width >= 40 && r.height >= 14) return node;
    }
    return el;
  }
  function markTarget(f) {
    if (f.kind !== 'radio' && f.kind !== 'checkgroup') {
      const el = tagged(f.hid);
      return el && shown(el);
    }
    const parts = halAll(`[data-hal-id^="${f.hid}:"]`).map(el => (el.labels && el.labels[0]) || el)
      .filter(el => el.checkVisibility());
    if (!parts.length) return null;
    let box = parts[0];
    while (box.parentElement && !parts.every(x => box.contains(x))) box = box.parentElement;
    return box;
  }

  const LABEL = { check: 'Check', answer: 'Answer', error: 'Refused' };
  const PANEL = `
    :host { all: initial; }
    .p { position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; width: 320px; max-height: 70vh;
         display: flex; flex-direction: column; font: 13px/1.4 system-ui, sans-serif; color: #1f2328;
         background: #fff; border: 1px solid #d0d7de; border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.18); }
    .h { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid #d0d7de; }
    .h b { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .counts { display: flex; gap: 6px; flex-wrap: wrap; padding: 8px 12px 0; }
    .c { padding: 1px 8px; border-radius: 999px; font-size: 12px; }
    .c.check { background: #fff4d6; color: #7a4d00; } .c.answer { background: #ffe1e1; color: #8a1111; }
    .c.ok { background: #dcf5e3; color: #11602e; } .c.edited { background: #dbe8ff; color: #183f8f; }
    .msg { padding: 8px 12px 0; color: #59636e; }
    ul { list-style: none; margin: 8px 0 0; padding: 0 6px; overflow: auto; }
    li button { all: unset; box-sizing: border-box; display: block; width: 100%; padding: 6px; border-radius: 6px; cursor: pointer; }
    li button:hover, li button:focus-visible { background: #f3f4f6; }
    .t { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; }
    .t.check { color: #9a6700; } .t.answer, .t.error { color: #cf222e; } .t.edited { color: #1f5fd6; }
    .n { color: #59636e; font-size: 12px; }
    .f { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 12px; border-top: 1px solid #d0d7de; margin-top: 8px; }
    .f button, .h button { font: inherit; padding: 4px 10px; border: 1px solid #d0d7de; border-radius: 6px; background: #f6f8fa;
                           color: inherit; cursor: pointer; }
    .f button.main { background: #1f6feb; border-color: #1f6feb; color: #fff; }
    .p.min ul, .p.min .counts, .p.min .msg, .p.min .f { display: none; }
    @media (prefers-color-scheme: dark) {
      .p { background: #1c2128; color: #e6edf3; border-color: #3d444d; } .h, .f { border-color: #3d444d; }
      li button:hover { background: #262c36; } .n, .msg { color: #9198a1; }
      .f button, .h button { background: #262c36; border-color: #3d444d; }
      .c.check { background: #3b2e0a; color: #f2cc60; } .c.answer { background: #4a1414; color: #ffa198; }
      .c.ok { background: #12361f; color: #7ee2a8; } .c.edited { background: #142b55; color: #a5c8ff; }
    }`;

  const ask = message => chrome.runtime.sendMessage({ hal: message }).catch(() => {});

  function drawPanel(p, items) {
    let host = document.getElementById('hal-panel');
    if (!host) {
      host = document.createElement('div');
      host.id = 'hal-panel';
      host.attachShadow({ mode: 'open' });
      document.documentElement.append(host);
    }
    const root = host.shadowRoot;
    const left = items.filter(i => i.target.getAttribute('data-hal-mark') !== 'edited');
    const count = state => items.filter(i => i.target.getAttribute('data-hal-mark') === state).length;
    const counts = [['answer', count('answer') + count('error'), 'to answer'], ['check', count('check'), 'to check'],
                    ['edited', count('edited'), 'changed by you'], ['ok', p.filled, 'filled']].filter(c => c[1]);
    const docs = { cv: 'CV', letter: 'Cover letter' };
    const minimised = root.querySelector('.p.min') !== null;
    root.innerHTML = `<style>${PANEL}</style><div class="p ${minimised ? 'min' : ''}">
      <div class="h"><b></b><button data-min title="Minimise">${minimised ? '+' : '\u2013'}</button></div>
      <div class="counts">${counts.map(([k, n, t]) => `<span class="c ${k}">${n} ${t}</span>`).join('')}</div>
      <div class="msg"></div><ul></ul>
      <div class="f"><button class="main" data-fill title="Fill whatever on this page is still empty">Fill empty fields</button>
        ${p.panel.docs.map(d => `<button data-doc="${d}">${docs[d] || d}</button>`).join('')}
        <button data-open>Open in Hal</button></div></div>`;
    root.querySelector('b').textContent = `Hal${p.panel.step > 1 ? ` \u00b7 step ${p.panel.step}` : ''} \u00b7 ${p.panel.company}`;
    root.querySelector('.msg').textContent = left.length
      ? `Nothing is sent until you press ${p.panel.final ? 'Submit' : 'Next'}.`
      : `All answered. Press ${p.panel.final ? 'Submit' : 'Next'} when you are happy.`;
    const list = root.querySelector('ul');
    for (const i of items) {
      const state = i.target.getAttribute('data-hal-mark');
      if (state === 'ok') continue;
      const li = document.createElement('li'), b = document.createElement('button');
      b.innerHTML = `<div class="t ${state}"></div><div class="q"></div><div class="n"></div>`;
      b.children[0].textContent = state === 'edited' ? 'Changed by you' : LABEL[state];
      b.children[1].textContent = i.f.label || '(unlabelled)';
      b.children[2].textContent = state === 'edited' ? '' : i.f.note;
      b.onclick = () => {
        i.target.scrollIntoView({ block: 'center', behavior: 'smooth' });
        const input = i.target.matches('input, textarea, select, [contenteditable]') ? i.target
          : i.target.querySelector('input:not([type=hidden]), textarea, select, [contenteditable]');
        if (input) input.focus({ preventScroll: true });
      };
      li.append(b);
      list.append(li);
    }
    root.querySelector('[data-min]').onclick = () => {
      root.querySelector('.p').classList.toggle('min');
      root.querySelector('[data-min]').textContent = root.querySelector('.p.min') ? '+' : '\u2013';
    };
    root.querySelector('[data-fill]').onclick = () => ask({ cmd: 'fill' });
    root.querySelector('[data-open]').onclick = () => ask({ cmd: 'open' });
    root.querySelectorAll('[data-doc]').forEach(b => b.onclick = () => ask({ cmd: 'doc', which: b.dataset.doc }));
  }

  function mark(p) {
    if (!document.getElementById('hal-marks')) {
      const style = document.createElement('style');
      style.id = 'hal-marks';
      style.textContent = MARKS;
      (document.head || document.documentElement).append(style);
    }
    for (const el of halAll('[data-hal-mark]')) el.removeAttribute('data-hal-mark');
    const items = [];
    for (const f of p.fields) {
      const target = markTarget(f);
      if (!target) continue;
      target.setAttribute('data-hal-mark', f.state);
      items.push({ f, target });
    }
    const panel = { ...p, filled: items.filter(i => i.f.state === 'ok').length };
    window.halMarked = { panel, items };
    drawPanel(panel, items);
    const first = items.find(i => i.f.state !== 'ok');
    if (first) first.target.scrollIntoView({ block: 'center' });
    return { marked: items.length };
  }

  // An answer the applicant changes is theirs: its mark turns blue and it leaves the list to check.
  if (!window.halListening) {
    window.halListening = true;
    const edited = e => {
      const target = e.target.closest && e.target.closest('[data-hal-mark]');
      if (!target || !e.isTrusted || target.getAttribute('data-hal-mark') === 'edited') return;
      target.setAttribute('data-hal-mark', 'edited');
      if (window.halMarked) drawPanel(window.halMarked.panel, window.halMarked.items);
    };
    document.addEventListener('input', edited, true);
    document.addEventListener('change', edited, true);
  }

  window.halPage = { read, press, fill, typeCode, mark, results: () => halResults() };
})();
