"""Small DOM operations run in a separate JavaScript world inside the owned browser."""

SNAPSHOT = r"""(options => {
    const short = (value, limit = 200) => String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
    const visible = e => {
        const r = e.getBoundingClientRect();
        const style = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const labelText = label => {
        const walker = document.createTreeWalker(label, NodeFilter.SHOW_TEXT);
        const parts = [];
        while (walker.nextNode()) {
            const node = walker.currentNode;
            if (!node.parentElement?.closest('input,textarea,select,button,[contenteditable]')) parts.push(node.textContent);
        }
        return parts.join(' ');
    };
    const selector = 'a[href],button,input,textarea,select,[contenteditable="true"],[role="button"],'
        + '[role="link"],[role="checkbox"],[role="radio"],[role="combobox"],[role="tab"],[role="menuitem"]';
    const elements = [];
    const roots = [document];
    let scanned = 0;
    while (roots.length && scanned < 20000) {
        const root = roots.shift();
        for (const e of root.querySelectorAll('*')) {
            if (++scanned > 20000) break;
            if (e.shadowRoot) roots.push(e.shadowRoot);
            if (e.matches(selector) && visible(e)) elements.push(e);
        }
    }
    const refs = new Map();
    const controls = elements.slice(options.offset, options.offset + options.limit).map((e, index) => {
        const id = options.prefix + '-' + index;
        refs.set(id, e);
        const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
            .map(id => document.getElementById(id)?.textContent || '').join(' ');
        const labels = e.labels ? Array.from(e.labels).map(labelText).join(' ') : '';
        const secret = e.type === 'password' || e.autocomplete === 'one-time-code';
        return {
            id, tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || '', type: e.type || '',
            name: short(e.getAttribute('aria-label') || labelled || labels || e.innerText || e.placeholder || e.title),
            value: secret ? '[private]' : short(e.value ?? (e.isContentEditable ? e.textContent : ''), 300),
            disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true',
            checked: !!e.checked || e.getAttribute('aria-checked') === 'true',
            href: e.tagName === 'A' ? short(e.href, 500) : '',
        };
    });
    globalThis.__toolkitControls = refs;
    const text = document.body?.innerText || '';
    return {
        title: short(document.title, 300), url: short(location.href, 1500),
        text: text.slice(options.text_offset, options.text_offset + 6000),
        text_offset: options.text_offset, text_total: text.length,
        controls, offset: options.offset, total: elements.length,
        more_controls: options.offset + options.limit < elements.length, scan_limited: scanned >= 20000,
    };
})"""

ACTION = r"""(options => {
    const e = globalThis.__toolkitControls?.get(options.id);
    if (!e || !e.isConnected) throw new Error('Control is stale. Take a fresh browser snapshot.');
    if (e.disabled || e.getAttribute('aria-disabled') === 'true') throw new Error('The control is disabled.');
    const send = type => e.dispatchEvent(new Event(type, {bubbles: true, composed: true}));
    if (options.action === 'click') {
        e.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
        const r = e.getBoundingClientRect();
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        const root = e.getRootNode();
        const hit = root.elementFromPoint ? root.elementFromPoint(x, y) : document.elementFromPoint(x, y);
        if (!hit || !(e === hit || e.contains(hit))) throw new Error('Another element covers this control. Inspect the page again.');
        if (options.dom_click) { e.click(); return {clicked: true}; }
        return {x, y};
    }
    if (options.action === 'fill') {
        if (e.readOnly) throw new Error('The field is read-only.');
        if (e.isContentEditable) e.textContent = options.value;
        else {
            const prototype = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
            if (!setter || !['INPUT', 'TEXTAREA'].includes(e.tagName) || ['file', 'checkbox', 'radio'].includes(e.type))
                throw new Error('This control is not a text field.');
            setter.call(e, options.value);
        }
        send('input'); send('change');
        return {filled: true};
    }
    if (options.action === 'select') {
        if (e.tagName !== 'SELECT' || !Array.from(e.options).some(o => o.value === options.value))
            throw new Error('Choose a value present in a native select control. Inspect custom menus instead.');
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(e, options.value);
        send('input'); send('change');
        return {selected: e.value};
    }
    if (options.action === 'check') {
        if (!['checkbox', 'radio'].includes(e.type)) throw new Error('This is not a native checkbox or radio control.');
        if (e.checked !== options.checked) e.click();
        if (e.checked !== options.checked) throw new Error('The control did not accept the requested state.');
        return {checked: e.checked};
    }
    throw new Error('Unknown browser action.');
})"""
