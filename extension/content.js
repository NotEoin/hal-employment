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

  async function comboboxOptions(el) {
    tap(el);
    await sleep(300);
    const options = [...document.querySelectorAll('[role=option]')].filter(visible)
      .map(o => o.innerText.trim()).filter(Boolean);
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    return options.slice(0, 400);
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

  function setChecked(el, want) {
    if (el.checked !== want) el.click();
  }

  async function fillOne(f, files) {
    const value = f.value;
    if (f.kind === 'radio' || f.kind === 'checkgroup') {
      const wanted = Array.isArray(value) ? value : [value];
      f.options.forEach((option, i) => {
        const el = tagged(`${f.hid}:${i}`);
        if (f.kind === 'radio' && wanted.includes(option)) setChecked(el, true);
        else if (f.kind === 'checkgroup') setChecked(el, wanted.includes(option));
      });
      return;
    }
    const el = tagged(f.hid);
    if (!el) throw new Error('the field is no longer on the page');
    if (f.kind === 'checkbox') setChecked(el, Boolean(value));
    else if (f.kind === 'file') {
      const file = files[value];
      const bytes = Uint8Array.from(atob(file.data), c => c.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], file.name, { type: file.type }));
      el.files = transfer.files;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (f.kind === 'select') {
      const option = [...el.options].find(o => o.text.replace(/\s+/g, ' ').trim() === value);
      if (!option) throw new Error(`no option "${value}"`);
      setValue(el, option.value);
    } else if (f.kind === 'combobox') {
      el.focus();
      tap(el);
      setValue(el, String(value));
      await sleep(600);
      const options = [...document.querySelectorAll('[role=option]')].filter(visible);
      const option = options.find(o => o.innerText.trim() === String(value)) || options[0];
      if (!option) throw new Error('no option appeared to choose');
      tap(option);
    } else {
      el.focus();
      setValue(el, String(value));
      el.dispatchEvent(new Event('blur', { bubbles: true }));
    }
  }

  async function fill(p) {
    const errors = {};
    for (const f of p.fields) {
      try { await fillOne(f, p.files); } catch (e) { errors[f.hid] = String(e.message || e).slice(0, 200); }
    }
    return { errors };
  }

  window.halPage = { read, press, fill, results: () => halResults() };
})();
