// Page readers shared by Hal's own browser (jobhunt/browser.py) and the extension.

// Every element matching the selector, including those inside open shadow roots.
function halAll(selector, root) {
  const out = [...(root || document).querySelectorAll(selector)];
  for (const host of (root || document).querySelectorAll('*'))
    if (host.shadowRoot) out.push(...halAll(selector, host.shadowRoot));
  return out;
}

function halVisible(el) {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
    && parseFloat(s.opacity || '1') > 0.05;
}

// A function rather than a constant: the extension runs this file again in the same page.
function halClean(t) {
  return (t || '').replace(/\s+/g, ' ').replace(/^\s*\*\s*|\s*\*\s*$/g, '').trim();
}

// Reads every control of the page into a field list: one field per question, with
// radio and checkbox options grouped under it. A field's label is the text that
// belongs to that control alone: its own <label>, or failing that the words before
// it inside the smallest box that holds no other question. Controls are tagged
// with data-hal-id, and keep the same id when the page is read again.
function halExtract() {
  const clean = halClean, visible = halVisible;
  const byId = ids => (ids || '').split(/\s+/).map(i => document.getElementById(i)).filter(Boolean);
  const textOf = ids => clean(byId(ids).map(e => e.innerText).join(' '));
  const GENERIC = /^(select|select\.\.\.|choose|search|type to search|toggle( flyout)?|open|clear|remove|required|optional|please select.*|select an option|select one|--+)$/i;
  const UPLOAD = /^(attach|attach files?|choose files?|upload( files?| a file)?|browse|select files?|drop.*|no file chosen|or drag and drop.*)$/i;
  // An open list may hide the rest of the form from screen readers, marking what it hides data-aria-hidden as the
  // aria-hidden library does (Ashby's location list, 2026-11-04); that is still the form, unless a dialog is open.
  const listOpen = Boolean(document.querySelector('[aria-expanded=true]:is([role=combobox], [aria-haspopup=listbox])'))
    && !halAll('[aria-modal=true], dialog[open]').some(visible);
  const HIDDEN = listOpen ? '[aria-hidden=true]:not([data-aria-hidden])' : '[aria-hidden=true]';
  const HELPER = 'option, select, button, script, style, svg, [role=option], [role=listbox], [role=alert], '
    + '[class*="error" i], [class*="invalid" i], [class*="counter" i], [class*="placeholder" i], '
    + '[class*="single-value" i], [class*="singleValue"], [class*="multi-value" i], ' + HIDDEN;

  // Language pickers and site searches sit in the page's own furniture, not in the form.
  const CHROME = 'header, nav, footer, [role=banner], [role=navigation], [role=contentinfo]';

  // Pass 1: the controls worth reading.
  const picked = [];
  const seen = new Set();
  const add = (el, info) => { if (!seen.has(el)) { seen.add(el); picked.push({ el, ...info }); } };
  for (const el of halAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
    const combo = el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list'
      || /select__input/.test(el.className) || el.hasAttribute('list');
    if (type === 'search' && !combo) continue;
    if (/recaptcha|captcha|honeypot|turnstile/i.test(el.name + ' ' + el.id)) continue;
    if (el.disabled) continue;
    const choice = type === 'radio' || type === 'checkbox';
    // Styled radios and checkboxes hide the input and show its label instead.
    const shown = visible(el) || (choice
      && ((el.labels && el.labels[0] && visible(el.labels[0])) || (el.parentElement && visible(el.parentElement))));
    if (type !== 'file' && !shown) continue;
    if (el.closest(HIDDEN) && type !== 'file') continue;
    if (el.closest('[role=search]') || (el.closest(CHROME) && !el.form)) continue;
    add(el, { type, combo, native: true });
  }
  // Widgets built from plain elements: dropdown buttons, ARIA radios and checkboxes, rich-text boxes.
  for (const el of halAll('[role=combobox], button[aria-haspopup=listbox], [role=radio], [role=checkbox], '
      + '[role=switch], [role=textbox], [contenteditable=true], [contenteditable=""]')) {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || !visible(el)) continue;
    if (el.closest(HIDDEN + ', [role=search]') || el.getAttribute('aria-disabled') === 'true') continue;
    if (el.closest(CHROME)) continue;
    if (el.querySelector('input:not([type=hidden]), textarea, select')) continue;
    const role = el.getAttribute('role') || (el.isContentEditable ? 'textbox' : 'combobox');
    add(el, { type: role, combo: role === 'combobox' || el.tagName === 'BUTTON', native: false });
  }
  // Yes/No answered with a pair of buttons over a hidden checkbox.
  const pairs = new Map();
  for (const p of picked) {
    if (p.type !== 'checkbox' || !p.native || visible(p.el)) continue;
    const box = p.el.parentElement;
    const buttons = box ? [...box.querySelectorAll('button')].filter(visible) : [];
    if (buttons.length === 2 && buttons.every(b => /^(yes|no|true|false)$/i.test(clean(b.innerText))))
      pairs.set(p.el, buttons);
  }
  picked.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);

  // Pass 2: group the options of one question.
  const groups = [];
  const groupOf = new Map();
  for (const p of picked) {
    const el = p.el;
    let key = null;
    if (p.native && (p.type === 'radio' || p.type === 'checkbox') && el.name && !pairs.has(el)) {
      const same = picked.filter(q => q.native && q.type === p.type && q.el.name === el.name && q.el.form === el.form);
      if (same.length > 1 || p.type === 'radio') key = p.type + ':' + el.name + ':' + [...document.forms].indexOf(el.form);
    } else if (!p.native && p.type === 'radio') {
      key = el.closest('[role=radiogroup]') || el.parentElement;
    }
    let g = key !== null ? groupOf.get(key) : null;
    if (!g) {
      g = { members: [], lead: p };
      groups.push(g);
      if (key !== null) groupOf.set(key, g);
    }
    g.members.push(p);
  }
  const owner = new Map();
  for (const g of groups) for (const m of g.members) {
    owner.set(m.el, g);
    for (const b of pairs.get(m.el) || []) owner.set(b, g);
  }

  // The smallest box around a question that holds no other question.
  const STOP = /^(FORM|BODY|HTML|MAIN)$/;
  const holdsOther = (node, g) => {
    for (const [el, other] of owner) if (other !== g && node.contains(el)) return true;
    return false;
  };
  const boxOf = g => {
    let box = g.lead.el, node = g.lead.el.parentElement;
    for (let depth = 0; node && depth < 12 && !STOP.test(node.tagName); depth++, node = node.parentElement) {
      if (!g.members.every(m => node.contains(m.el))) { box = node; continue; }
      if (holdsOther(node, g) || node.innerText.length > 2500) break;
      box = node;
    }
    return box;
  };
  const ownLabel = el => {
    if (el.labels && el.labels.length) return clean([...el.labels].map(l => l.innerText).join(' '));
    if (el.getAttribute('aria-labelledby')) return textOf(el.getAttribute('aria-labelledby'));
    const aria = clean(el.getAttribute('aria-label'));
    return GENERIC.test(aria) ? '' : aria;
  };
  // The words inside `box` before (or after) the question's first control, leaving out its options and helpers.
  const around = (box, g, after) => {
    const skip = new Set();
    for (const m of g.members) {
      skip.add(m.el);
      [...(m.el.labels || [])].forEach(l => skip.add(l));
      if (g.members.length > 1) byId(m.el.getAttribute('aria-labelledby')).forEach(l => skip.add(l));
      for (const b of pairs.get(m.el) || []) skip.add(b);
    }
    const first = g.members[0].el, parts = [];
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.nodeValue.trim();
      const parent = node.parentElement;
      if (!text || !parent || parent.closest(HELPER)) continue;
      let skipped = false;
      for (const s of skip) if (s.contains(parent)) { skipped = true; break; }
      if (skipped || !visible(parent)) continue;
      const before = Boolean(first.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_PRECEDING);
      if (before !== after) parts.push(text);
    }
    return clean(parts.join(' '));
  };
  // When a box holds a lot of words, the question is the labelled piece nearest above the control.
  const nearest = (box, g, before) => {
    if (before.length <= 160) return before;
    const first = g.members[0].el;
    const marked = [...box.querySelectorAll('label, legend, h1, h2, h3, h4, h5, h6, [class*="label" i], [class*="title" i], [class*="question" i]')]
      .filter(c => (first.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_PRECEDING) && !c.contains(first)
        && visible(c) && clean(c.innerText) && clean(c.innerText).length <= 200);
    return marked.length ? clean(marked[marked.length - 1].innerText) : before;
  };
  // The nearest block of words above the box that holds no control of its own.
  const above = (box, g) => {
    for (let node = box, depth = 0; node && depth < 5 && !STOP.test(node.tagName); depth++, node = node.parentElement) {
      for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) {
        if (holdsOther(sib, g) || sib.querySelector('input:not([type=hidden]), textarea, select')) return '';
        const text = visible(sib) ? clean(sib.innerText) : '';
        if (text) return text.length <= 400 ? text : '';
      }
    }
    return '';
  };
  const errorIn = (box, el) => {
    const ids = [el.getAttribute('aria-errormessage'),
                 el.getAttribute('aria-invalid') === 'true' ? el.getAttribute('aria-describedby') : ''].filter(Boolean).join(' ');
    const named = textOf(ids);
    if (named) return named;
    const found = [...box.querySelectorAll('[role=alert], [class*="error" i], [class*="invalid" i]')]
      .filter(e => visible(e) && !e.querySelector('input, textarea, select')).map(e => clean(e.innerText)).filter(Boolean);
    return found.length ? found[0].slice(0, 200) : '';
  };
  const requiredEl = el => el.required || el.getAttribute('aria-required') === 'true';
  const PLACEHOLDER = /^(select|choose|please (select|choose)|--)/i;

  let n = 0;
  for (const el of halAll('[data-hal-id]')) {
    const m = /^h(\d+)/.exec(el.getAttribute('data-hal-id'));
    if (m) n = Math.max(n, +m[1] + 1);
  }
  const hidFor = el => {
    const had = /^(h\d+)(:\d+)?$/.exec(el.getAttribute('data-hal-id') || '');
    return had ? had[1] : 'h' + (n++);
  };

  // Section headings, in page order: "Referee 1" or "Education" says whose details a bare "Name" or "Start date" asks for.
  const heads = halAll('h1, h2, h3, h4, h5, legend, [role=heading], [class*="section-title" i], [class*="sectionTitle"], '
      + '[class*="section-header" i], [class*="sectionHeader"]')
    .filter(h => visible(h) && clean(h.innerText) && clean(h.innerText).length <= 80);
  const sectionOf = (el, label) => {
    let found = '';
    for (const h of heads) {
      if (!(h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) || h.contains(el)) continue;
      // A legend heads only its own fieldset.
      if (h.tagName === 'LEGEND' && !(h.parentElement && h.parentElement.contains(el))) continue;
      const text = clean(h.innerText);
      if (text !== label) found = text;
    }
    return found;
  };

  // The site's own job search or job-alert sign-up, which no label says: "search" or "alert", or "" for neither.
  // ProductionBase wraps its whole page in one form, so the search's boxes sat in the same form as everything
  // else, labelled "Job Types" and "Industry" (testers, 2026-11-04). Names are matched narrowly, so an
  // application's own searchable dropdown ("select__search") is never taken for the site's search.
  const SEARCH_NAME = /job.?search|search.?(box|bar|form|section|panel|area|field|input|query)|site.?search|searchkeyword/i;
  const ALERT_WORDS = /job alerts?|alerts? (direct )?to your inbox|similar (new )?jobs|newsletter|jobs by email/i;
  const siteBox = el => {
    const form = el.form;
    if (el.closest('[role=search]') || SEARCH_NAME.test(`${el.name} ${el.id}`)
        || (form && /search/i.test(form.getAttribute('action') || '') && !/apply|application/i.test(form.getAttribute('action') || ''))) {
      return 'search';
    }
    for (let n = el.parentElement, depth = 0; n && depth < 5 && !STOP.test(n.tagName); n = n.parentElement, depth++) {
      if (SEARCH_NAME.test(`${typeof n.className === 'string' ? n.className : ''} ${n.id}`)) return 'search';
      const text = n.innerText || '';
      const boxes = n.querySelectorAll('input:not([type=hidden]):not([type=submit]), textarea, select').length;
      if (text.length < 400 && boxes <= 3 && ALERT_WORDS.test(text)) return 'alert';
    }
    return '';
  };

  const fields = [];
  for (const g of groups) {
    const lead = g.lead, el = lead.el, box = boxOf(g);
    const hid = hidFor(el);
    const said = around(box, g, false), after = around(box, g, true);
    const buttons = pairs.get(el);
    // A question with options is often a paragraph; a lone control's box can hold its neighbours' words too.
    const before = g.members.length > 1 || lead.type === 'radio' || buttons ? said : nearest(box, g, said);
    let f;
    if (g.members.length > 1 || lead.type === 'radio' || buttons) {
      const frame = el.closest('fieldset');
      const legend = frame && !holdsOther(frame, g) && frame.querySelector('legend');
      const group = el.closest('[role=radiogroup], [role=group]');
      const named = group && !holdsOther(group, g)
        ? (textOf(group.getAttribute('aria-labelledby')) || clean(group.getAttribute('aria-label'))) : '';
      const options = [], current = [];
      // The hidden box under a pair of buttons carries the id, so the question keeps it on the next read.
      if (buttons) el.setAttribute('data-hal-id', hid);
      (buttons || g.members.map(m => m.el)).forEach((c, i) => {
        c.setAttribute('data-hal-id', hid + ':' + i);
        const text = buttons ? clean(c.innerText) : (ownLabel(c) || clean(c.innerText) || clean(c.value));
        options.push(text);
        const on = buttons
          ? /(^|[\s_-])(active|selected|checked)/i.test(c.className) || c.getAttribute('aria-pressed') === 'true'
          : (c.checked === true || c.getAttribute('aria-checked') === 'true');
        if (on) current.push(text);
      });
      const kind = lead.type === 'checkbox' && !buttons ? 'checkgroup' : 'radio';
      f = { hid, kind, type: lead.type, label: (legend && clean(legend.innerText)) || named || before || above(box, g),
            options, current: kind === 'radio' ? (current[0] || '') : current,
            required: g.members.some(m => requiredEl(m.el)), custom: Boolean(buttons) || !lead.native };
    } else {
      el.setAttribute('data-hal-id', hid);
      const type = lead.type;
      let kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : 'text';
      if (type === 'file') kind = 'file';
      else if (type === 'checkbox' || type === 'switch') kind = 'checkbox';
      else if (lead.combo) kind = 'combobox';
      else if (type === 'textbox' && !lead.native) kind = 'textarea';
      let label = ownLabel(el);
      if (kind === 'checkbox') {
        // A lone tick box is named by the words beside it; a bare "Yes" needs the question above.
        if (!label || /^(yes|on|i agree|true)$/i.test(label)) label = clean([before, label || after].join(' ')) || above(box, g);
      } else {
        // Upload widgets label the button ("Attach", "Choose file"); the question names the document.
        if (!label || (kind === 'file' && UPLOAD.test(label)))
          label = before || (kind === 'file' && after.slice(0, 80)) || above(box, g) || label;
        if (!label) label = clean(el.getAttribute('placeholder')) || clean(el.getAttribute('title'))
          || clean((el.name || el.id || '').replace(/\]$/, '').replace(/.*\[/, '').replace(/[_-]+/g, ' '));
      }
      f = { hid, kind, type, label, required: requiredEl(el), name: el.name || el.id || '', custom: !lead.native };
      if (kind === 'file') {
        f.current = [...el.files].map(x => x.name).join(', ');
        f.context = clean([el.id, el.name, before, el.accept || ''].join(' '));
      } else if (kind === 'checkbox') {
        f.current = lead.native ? el.checked : el.getAttribute('aria-checked') === 'true';
        f.context = before;
      } else if (kind === 'select') {
        f.options = [...el.options].filter((o, i) => !(i === 0 && !o.value) && clean(o.text) && !PLACEHOLDER.test(clean(o.text)))
          .map(o => clean(o.text));
        if (el.multiple) {
          // A list that takes several answers holds them all.
          f.multiple = true;
          f.current = [...el.selectedOptions].map(o => clean(o.text)).filter(t => f.options.includes(t));
        } else {
          const chosen = el.selectedOptions[0];
          f.current = chosen && f.options.includes(clean(chosen.text)) ? clean(chosen.text) : '';
        }
      } else if (kind === 'combobox') {
        // Some widgets keep a plain <select> or <datalist> beside the styled one, holding every option.
        const mirror = [...box.querySelectorAll('select')].find(s => s !== el && s.options.length > 1)
          || (el.getAttribute('list') && document.getElementById(el.getAttribute('list')));
        f.options = mirror ? [...mirror.options].map(o => clean(o.text || o.value)).filter(t => t && !PLACEHOLDER.test(t)) : [];
        const shown = box.querySelector('[class*="single-value" i], [class*="singleValue"], [class*="selected-value" i]');
        const own = lead.native ? el.value : clean(el.innerText);
        f.current = shown ? clean(shown.innerText) : (GENERIC.test(own) ? '' : own);
        f.typeable = lead.native && !el.readOnly;
      } else {
        f.current = lead.native ? el.value : clean(el.innerText);
        if (el.maxLength > 0) f.maxlength = el.maxLength;
      }
    }
    const marked = clean([f.label, before].join(' '));
    if (!f.required) f.required = /\*|\(required\)/i.test(marked.slice(-12)) || /\*\s*$/.test(clean(box.innerText).slice(0, f.label.length + 6))
      || Boolean(box.querySelector('[class*="required" i]:not(input):not(select):not(textarea), abbr[title*="required" i]'));
    if (/\(optional\)/i.test(f.label)) f.required = false;
    f.label = clean(f.label.replace(/\s*\(required\)\s*$/i, '')).slice(0, 900);
    const help = clean([textOf(el.getAttribute('aria-describedby') || ''), after].filter(Boolean).join(' '));
    if (help && help !== f.label && !GENERIC.test(help) && f.kind !== 'checkbox') f.help = help.slice(0, 300);
    const problem = errorIn(box, el);
    if (problem || el.getAttribute('aria-invalid') === 'true') f.invalid = problem || 'marked invalid';
    f.form = el.form ? [...document.forms].indexOf(el.form) : -1;
    const site = siteBox(el);
    if (site) f.site_box = site;
    const section = sectionOf(el, f.label);
    if (section) f.section = section;
    f.sig = [f.kind, f.name || '', f.label.slice(0, 120)].join('|');
    fields.push(f);
  }
  return fields;
}

// What the page as a whole shows, for deciding where to go next: its buttons and
// links (tagged with data-hal-nav so one can be pressed), headings, messages and walls.
// Where a control sits in the page's own furniture: 'footer' (a footer, or a bar of links near the end of the
// page, as Hacker News builds its "Guidelines | FAQ | ... | Apply to YC" row from plain spans), 'nav' (a header
// or menu, where some sites keep their real Apply button), or '' for the page's content.
const HAL_BAR_LINKS = 5;      // links in a row before it counts as a bar of links
const HAL_BAR_SHARE = 0.7;    // the part of the row's words that are link words
const HAL_PAGE_END = 0.7;     // how far down the page a footer's bar starts
function halFurniture(el) {
  if (el.closest('footer, [role=contentinfo]')) return 'footer';
  // The content's own height: a short page sits in a taller window, and its last row is still its end.
  const height = Math.max(document.body.scrollHeight || document.documentElement.scrollHeight, 1);
  const top = el.getBoundingClientRect().top + window.scrollY;
  for (let n = el.parentElement, up = 0; n && n !== document.body && up < 3; n = n.parentElement, up++) {
    const links = [...n.querySelectorAll('a[href]')];
    const words = halClean(n.innerText).length;
    if (links.length >= HAL_BAR_LINKS && words
        && links.reduce((sum, a) => sum + halClean(a.innerText).length, 0) / words >= HAL_BAR_SHARE) {
      if (top / height >= HAL_PAGE_END) return 'footer';
      break;
    }
  }
  return el.closest('nav, header, [role=navigation], [role=banner]') ? 'nav' : '';
}

function halSurvey() {
  const clean = halClean, visible = halVisible;
  const nameOf = el => clean(el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || '');
  const controls = [];
  let n = 0;
  for (const el of halAll('[data-hal-nav]')) el.removeAttribute('data-hal-nav');
  for (const el of halAll('a[href], button, [role=button], [role=link], input[type=submit], input[type=button]')) {
    if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const name = nameOf(el);
    if (!name || name.length > 90) continue;
    const id = 'n' + (n++);
    el.setAttribute('data-hal-nav', id);
    const link = el.tagName === 'A' || el.getAttribute('role') === 'link';
    controls.push({ id, name, link, href: link ? (el.href || '') : '', submit: el.type === 'submit',
                    inForm: Boolean(el.closest('form')),
                    inNav: Boolean(halFurniture(el)), inFooter: halFurniture(el) === 'footer' });
  }
  const alerts = halAll('[role=alert], [aria-live=assertive], [class*="error" i], [class*="invalid-feedback" i]')
    .filter(e => visible(e) && !e.querySelector('input, textarea, select, form'))
    .map(e => clean(e.innerText)).filter(t => t && t.length < 300);
  const turnstile = halAll("input[name='cf-turnstile-response']");
  return {
    url: location.href, title: document.title, controls,
    headings: halAll('h1, h2, h3').filter(visible).slice(0, 8).map(h => clean(h.innerText)).filter(Boolean),
    text: (document.body ? document.body.innerText : '').slice(0, 40000),
    alerts: [...new Set(alerts)].slice(0, 12),
    password: halAll('input[type=password]').some(visible),
    invalid: halAll('[aria-invalid=true]').filter(visible).length,
    captcha: halAll("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare.com'], "
      + "input[name='cf-turnstile-response'], .cf-turnstile, .g-recaptcha, .h-captcha").length > 0,
    // A picture puzzle on show, or a Turnstile whose hidden answer is still empty.
    challenge: halAll("iframe[src*='bframe'], iframe[src*='hcaptcha']").filter(visible)
      .some(f => f.getBoundingClientRect().height > 100) || turnstile.some(e => !e.value),
    stayed: halAll('[data-hal-step]').some(visible),
  };
}

// Pop-ups that ask for something other than the application, such as a job-alert or newsletter sign-up, lie
// over the advert and catch its clicks (Adzuna's, 2026-11-04). Closes the first one on show with its own
// close control, or with Escape when it has none, and returns what it pressed, or '' when nothing was in the
// way. A pop-up that could hold the application (an upload, or three or more boxes) is never closed, and a
// button that would send a form inside the pop-up is never the one pressed.
// A pop-up is found by its role or class, or from a close control upward to the fixed layer it sits in:
// Adzuna's pop-up library names its layers "mfp-wrap" and "mfp-container", with no role and no word a class
// match would know (2026-11-04).
function halPopup() {
  const clean = halClean, visible = halVisible;
  const ASKS = /job alert|email alert|similar (new )?jobs|jobs like this|newsletter|subscribe|sign up for|stay (up to date|in the loop)|don.t miss|never miss|get (the )?latest|notifications|download (our|the) app|get the app|jobs by email|join our talent/i;
  const CLOSE = /^(close|close (this )?(dialog|modal|pop-?up|window)|dismiss|no,? thanks?( you)?(,? .{0,40})?|not now|maybe later|skip|continue browsing|continue without .*|\u00d7|\u2715|\u2716|x)$/i;
  const LAYER = '[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open]';
  const nameOf = el => clean(el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || '');
  const sends = el => el.closest('form') && (el.type === 'submit' || (el.tagName === 'BUTTON' && !el.getAttribute('type')));
  const closeClass = el => typeof el.className === 'string' && /(^|[\s_-])close([\s_-]|$)/i.test(el.className);
  const closer = el => visible(el) && !sends(el) && (CLOSE.test(nameOf(el)) || closeClass(el));
  // a box raised over the page, as Adzuna's job-alert box under its Apply button is (absolute, z-index 50)
  const RAISED_Z = 10;
  const raised = n => { const s = getComputedStyle(n); return s.position === 'absolute' && +s.zIndex >= RAISED_Z; };
  const layerOf = el => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      if (n.matches(LAYER) || getComputedStyle(n).position === 'fixed' || raised(n)) return n;
    }
    return null;
  };
  const named = halAll(LAYER + ', [class*="modal" i], [class*="popup" i], [class*="pop-up" i], [class*="overlay" i], '
      + '[id*="modal" i], [id*="popup" i]')
    .filter(el => visible(el) && (el.matches(LAYER) || ['fixed', 'absolute', 'sticky'].includes(getComputedStyle(el).position)));
  const found = halAll('button, [role=button], a, [aria-label], [title]').filter(closer).map(layerOf).filter(Boolean);
  for (const box of new Set([...named, ...found])) {
    if (box.querySelector('input[type=file]')) continue;
    const inputs = [...box.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select')]
      .filter(visible);
    if (inputs.length >= 3 || !ASKS.test(clean(box.innerText).slice(0, 1500))) continue;
    const close = [...box.querySelectorAll('button, [role=button], a, [aria-label], [title]')].find(closer);
    if (close) { close.click(); return nameOf(close) || 'close'; }
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
    return 'Escape';
  }
  return '';
}

const HAL_ITEMS = '[role=option], [class*="__option"], [id*="-option-"], [role=menuitemradio], [role=menuitem], li[data-value]';

// Marks the list items on show before a dropdown is opened, so a list left open by
// another control is not read as this one's options.
function halStale() {
  for (const o of halAll('[data-hal-stale]')) o.removeAttribute('data-hal-stale');
  for (const o of halAll(HAL_ITEMS)) if (halVisible(o)) o.setAttribute('data-hal-stale', '');
  return true;
}

// The options of an open dropdown: the list the control names, else any list opened since halStale.
// Each is tagged with data-hal-opt so the one chosen can be pressed.
function halOptions(hid) {
  // The control may sit inside a page component (an open shadow root), where its list is too.
  const el = halAll(`[data-hal-id="${hid}"]`)[0];
  const home = el && el.getRootNode && el.getRootNode().getElementById ? el.getRootNode() : document;
  const ITEMS = HAL_ITEMS;
  let pool = [], named = false;
  for (const attr of ['aria-controls', 'aria-owns']) {
    const list = el && el.getAttribute(attr) && home.getElementById(el.getAttribute(attr));
    if (!list) continue;
    // A list the control names is the only place its options can be.
    named = true;
    pool = [...list.querySelectorAll(ITEMS)];
    if (!pool.length) pool = [...list.querySelectorAll('li')];
    if (!pool.length && list.getAttribute('role') === 'listbox') pool = [...list.children];
    if (pool.length) break;
  }
  if (!pool.length && !named) pool = halAll(ITEMS).filter(o => !o.hasAttribute('data-hal-stale'));
  if (!pool.length && !named) pool = halAll('[role=listbox] li, ul[class*="menu" i] li, ul[class*="dropdown" i] li, '
    + 'ul[class*="results" i] li, ul[class*="suggest" i] li, [class*="autocomplete" i] li');
  for (const o of halAll('[data-hal-opt]')) o.removeAttribute('data-hal-opt');
  // An item wrapped in another item is one option, not two.
  pool = pool.filter(o => !pool.some(other => other !== o && other.contains(o)));
  const out = [];
  for (const o of pool) {
    if (!halVisible(o) || o.getAttribute('aria-disabled') === 'true') continue;
    const text = halClean(o.innerText);
    if (!text || /^(no (options|results|matches|selection)|loading|searching|type to search)/i.test(text)) continue;
    o.setAttribute('data-hal-opt', String(out.length));
    out.push(text);
    if (out.length >= 400) break;
  }
  return out;
}

// The words the page shows, with those inside page components (open shadow roots), where an upload
// widget may list the file it took.
function halText() {
  const parts = [document.body ? document.body.innerText : ''];
  for (const host of halAll('*')) if (host.shadowRoot) parts.push(host.shadowRoot.textContent || '');
  return parts.join('\n').slice(0, 80000);
}

// Reads a DuckDuckGo results page into {url, title, snippet} rows.
function halResults() {
  return [...document.querySelectorAll("article[data-testid='result']")].map(a => {
    const title = a.querySelector("a[data-testid='result-title-a']"), snippet = a.querySelector("[data-result='snippet']");
    return { url: title ? title.href : '', title: title ? title.innerText.trim() : '',
             snippet: snippet ? snippet.innerText.replace(/\s+/g, ' ').trim() : '' };
  }).filter(r => r.url);
}
