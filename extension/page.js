// Page readers shared by Hal's own browser (jobhunt/browser.py) and the extension.

// Reads every visible control into a field list, grouping radio and checkbox
// options under their question. Hidden helper inputs (react-select's required
// shadow input, captcha responses, phone-country search boxes) are skipped.
function halExtract() {
  const clean = t => (t || '').replace(/\s+/g, ' ').replace(/^\s*\*\s*|\s*\*\s*$/g, '').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
      && parseFloat(s.opacity || '1') > 0.05;
  };
  const textOf = ids => (ids || '').split(/\s+/).map(i => document.getElementById(i))
    .filter(Boolean).map(e => e.innerText).join(' ');
  const ownLabel = el => {
    if (el.labels && el.labels.length) return clean([...el.labels].map(l => l.innerText).join(' '));
    if (el.getAttribute('aria-labelledby')) return clean(textOf(el.getAttribute('aria-labelledby')));
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    return '';
  };
  const LABELISH = 'label, legend, h3, h4, [class*="label" i], [class*="question" i], [class*="title" i]';
  const question = el => {
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector('legend');
      if (lg && clean(lg.innerText)) return clean(lg.innerText);
    }
    const grp = el.closest('[role=radiogroup], [role=group]');
    if (grp) {
      const t = clean(textOf(grp.getAttribute('aria-labelledby')) || grp.getAttribute('aria-label'));
      if (t) return t;
    }
    let node = el.parentElement;
    for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
      for (const cand of node.querySelectorAll(':scope > ' + LABELISH.split(', ').join(', :scope > '))) {
        const optionLabel = cand.tagName === 'LABEL' && cand.control
          && ['radio', 'checkbox'].includes(cand.control.type);
        if (!cand.contains(el) && !optionLabel && !cand.querySelector('input') && clean(cand.innerText))
          return clean(cand.innerText);
      }
    }
    return '';
  };
  const required = el => el.required || el.getAttribute('aria-required') === 'true'
    || /\*\s*$/.test((el.labels && el.labels[0] ? el.labels[0].innerText : '') || '');

  const fields = [], groups = {};
  let n = 0;
  const controls = document.querySelectorAll('input, textarea, select');
  for (const el of controls) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'search', 'image', 'reset'].includes(type)) continue;
    if (/recaptcha|captcha|honeypot/i.test(el.name + ' ' + el.id)) continue;
    // Styled radios and checkboxes hide the input and show its label instead.
    const shown = visible(el) || ((type === 'radio' || type === 'checkbox')
      && ((el.labels && el.labels[0] && visible(el.labels[0])) || (el.parentElement && visible(el.parentElement))));
    if (type !== 'file' && !shown) continue;
    if (el.closest('[aria-hidden=true]') && type !== 'file') continue;
    if (el.closest('[role=search]')) continue;
    const owner = el.form ? [...document.forms].indexOf(el.form) : -1;
    if (type === 'radio' || type === 'checkbox') {
      const key = el.name ? type + ':' + el.name : null;
      const q = question(el);
      const own = ownLabel(el);
      const opt = own || clean(el.value);
      if (key && document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1) {
        let g = groups[key];
        if (!g) {
          g = groups[key] = { hid: 'h' + (n++), kind: type === 'radio' ? 'radio' : 'checkgroup',
                              label: q, required: false, options: [], current: [], form: owner };
          fields.push(g);
        }
        el.setAttribute('data-hal-id', g.hid + ':' + g.options.length);
        g.options.push(opt);
        g.required = g.required || required(el);
        if (el.checked) g.current.push(opt);
        continue;
      }
      const hid = 'h' + (n++);
      el.setAttribute('data-hal-id', hid);
      const label = (own && !/^(yes|on|i agree)$/i.test(own)) ? own : (q || opt);
      fields.push({ hid, kind: 'checkbox', label, context: q, required: required(el), current: el.checked,
                    form: owner });
      continue;
    }
    const hid = 'h' + (n++);
    el.setAttribute('data-hal-id', hid);
    let kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : 'text';
    if (type === 'file') kind = 'file';
    if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list'
        || /select__input/.test(el.className)) kind = 'combobox';
    let label = ownLabel(el);
    // Upload widgets label the button ("Attach", "Choose file"); the question names the document.
    if (!label || (kind === 'file' && /^(attach|choose files?|upload( files?)?|browse|select files?)$/i.test(label)))
      label = question(el) || label;
    const f = { hid, kind, type, label, required: required(el), form: owner,
                name: el.name || el.id || '', current: kind === 'file' ? '' : el.value };
    if (kind === 'file') f.context = clean([el.id, el.name, question(el), el.accept || ''].join(' '));
    if (kind === 'select') f.options = [...el.options].map(o => clean(o.text))
      .filter(t => t && !/^(select|choose|please select)/i.test(t));
    if (el.maxLength > 0) f.maxlength = el.maxLength;
    fields.push(f);
  }
  return fields;
}

// Reads a DuckDuckGo results page into {url, title, snippet} rows.
function halResults() {
  return [...document.querySelectorAll("article[data-testid='result']")].map(a => {
    const title = a.querySelector("a[data-testid='result-title-a']"), snippet = a.querySelector("[data-result='snippet']");
    return { url: title ? title.href : '', title: title ? title.innerText.trim() : '',
             snippet: snippet ? snippet.innerText.replace(/\s+/g, ' ').trim() : '' };
  }).filter(r => r.url);
}
