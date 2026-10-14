// Runs inside a job tab Hal opened: reports what the page shows, presses Apply
// and Next controls, and types approved answers into the form. It never
// presses a submit button. Patterns arrive from jobhunt/extension.py so the
// rules live in one place.

(() => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const rx = source => new RegExp(source, 'i');
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const nameOf = el => (el.getAttribute('aria-label') || el.innerText || el.value || '').replace(/\s+/g, ' ').trim();
  const controls = (selector, pattern) =>
    [...document.querySelectorAll(selector)].filter(el => visible(el) && rx(pattern).test(nameOf(el)));
  const BUTTONS = "button, [role=button], input[type=submit], input[type=button]";
  // Widgets such as react-select open on mouse-down, which a bare click() never sends.
  const tap = el => ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(type =>
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window })));
  const tagged = hid => document.querySelector(`[data-hal-id="${CSS.escape(hid)}"]`);

  // Cookie banners block clicks underneath them; refusing optional cookies is preferred.
  async function dismissBanners(patterns) {
    for (const pattern of patterns) {
      const button = controls(BUTTONS, pattern)[0];
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
  function closeList(el) {
    el.blur();
    if (document.querySelector('[data-hal-opt]'))
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
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
    if (p.banners) await dismissBanners(p.banners);
    const fields = halExtract();
    if (p.options) {
      for (const f of fields) if (f.kind === 'combobox') f.options = await comboboxOptions(tagged(f.hid));
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

  function press(p) {
    if (p.kind === 'named') {
      // A control the helper chose by name, on a page with no Apply button it recognised.
      const named = [...document.querySelectorAll("a[href], [role=link], " + BUTTONS)].find(el => visible(el) && nameOf(el) === p.name);
      if (named) named.click();
      return { pressed: Boolean(named) };
    }
    const found = p.kind === 'apply' ? controls("a[href], [role=link], " + BUTTONS, p.pattern) : controls(BUTTONS, p.pattern);
    const control = p.kind === 'apply' ? found[0] : found[found.length - 1];
    if (!control) return { pressed: false };
    if (p.kind === 'next') {
      // Marks this step's controls so a form that refuses to move on can be told from a new step.
      document.querySelectorAll('[data-hal-id]').forEach(e => e.setAttribute('data-hal-step', ''));
    }
    control.click();
    return { pressed: true };
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
  function setChecked(el, want) {
    if (isOn(el) === want) return;
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

  async function fillOne(f, files) {
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
        if (el && (chosen || f.kind === 'checkgroup')) setChecked(el, chosen);
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
    if (f.kind === 'checkbox') setChecked(el, Boolean(value));
    else if (f.kind === 'select') {
      const options = [...el.options];
      const index = bestOption(options.map(o => o.text), value);
      if (index < 0) throw new Error(`no option "${value}"`);
      el.focus();
      setValue(el, options[index].value);
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

  async function fill(p) {
    const errors = {};
    for (const f of p.fields) {
      try { await fillOne(f, p.files); } catch (e) { errors[f.hid] = String(e.message || e).slice(0, 200); }
      // One answer at a time, at about the pace of a quick paste, so each is taken before the next.
      await sleep(120 + Math.random() * 200);
    }
    return { errors };
  }

  window.halPage = { read, press, fill, results: () => halResults() };
})();
