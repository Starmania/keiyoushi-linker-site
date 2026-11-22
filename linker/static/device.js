// "Check on a device" on a folder page: fills the links and the account's commands (each <li data-tpl>) from the
// chosen source and the title to try. The command list itself is saved with the account, on the account page.
(() => {
    const root = document.getElementById('device');
    const data = JSON.parse(document.getElementById('device-data').textContent);
    const $ = (id) => document.getElementById(id);

    // One shell word, quoted only when it needs to be.
    const sh = (s) => (/^[\w@%+=:,./-]+$/.test(String(s)) ? String(s) : `'${String(s).replace(/'/g, `'\\''`)}'`);
    // `adb shell` and `ssh host` hand the command to the device's shell again: one quoting per shell.
    const sh2 = (s) => sh(sh(s));
    const QUOTE = { ':sh': sh, ':sh2': sh2 };
    const PLACEHOLDER = /\{(\w+)(:sh2?)?\}/g;
    const fill = (tpl, v) => tpl.replace(PLACEHOLDER, (m, k, q) => (k in v ? (q ? QUOTE[q](v[k]) : v[k]) : m));
    const MISSING = { query: 'Type a title above', apk: 'No published APK for this extension' };

    function values() {
        const s = data.sources[$('dev-source') ? +$('dev-source').value : 0];
        return {
            url: s.base_url,
            query: $('dev-query').value.trim(),
            pkg: data.pkg || '',
            apk: data.apk || '',
            app: data.app || 'app.mihon',
            name: s.name,
            source_id: s.source_id,
            lang: s.lang,
        };
    }

    function render() {
        const v = values();
        for (const a of root.querySelectorAll('a[data-href]')) a.href = fill(a.dataset.href, v);
        for (const li of root.querySelectorAll('li[data-tpl]')) {
            const tpl = li.dataset.tpl;
            // A placeholder with no value (no title typed, no APK published) would leave a broken command.
            const empty = [...tpl.matchAll(PLACEHOLDER)].map((m) => m[1]).find((k) => k in v && !v[k]);
            li.querySelector('code').textContent = empty ? MISSING[empty] || `No ${empty} for this source` : fill(tpl, v);
            li.classList.toggle('disabled', !!empty);
            li.querySelector('.copy').disabled = !!empty;
        }
    }

    root.addEventListener('click', async (ev) => {
        const btn = ev.target.closest('button.copy');
        if (!btn) return;
        const code = btn.parentElement.querySelector('code');
        try {
            await navigator.clipboard.writeText(code.textContent);
        } catch {
            // The clipboard API needs a secure context: fall back to selecting the text.
            getSelection().selectAllChildren(code);
            return;
        }
        btn.textContent = 'Copied';
        setTimeout(() => (btn.textContent = 'Copy'), 1200);
    });

    $('dev-query').addEventListener('input', render);
    $('dev-source')?.addEventListener('change', render);
    render();
})();
