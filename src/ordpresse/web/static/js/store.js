/**
 * Plugin Store, Mirror Authority, Live Search, & Book Archive Controller
 */

async function loadStoreMirrors(forceRefresh = false) {
    const mirrorsContainer = document.getElementById('mirrors-container');
    const mirrorCacheBadge = document.getElementById('mirror-cache-badge');
    if (!mirrorsContainer) return;

    try {
        if (mirrorCacheBadge) mirrorCacheBadge.textContent = forceRefresh ? 'Måler...' : 'Indlæser...';
        const url = forceRefresh ? '/api/v1/store/mirrors?refresh=true' : '/api/v1/store/mirrors';
        const res = await fetch(url);
        if (!res.ok) throw new Error('Kunne ikke hente spejle');
        const data = await res.json();

        if (mirrorCacheBadge) mirrorCacheBadge.textContent = `TTL: ${data.ttl_seconds || 3600}s`;
        mirrorsContainer.innerHTML = '';

        // Libgen section
        const libgenDiv = document.createElement('div');
        libgenDiv.className = 'p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-2';
        libgenDiv.innerHTML = `
            <div class="flex items-center justify-between">
                <strong class="text-white text-xs font-semibold">📚 Libgen Spejle (Primær)</strong>
                <span class="text-[10px] text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/20">Rå HTML</span>
            </div>
            <div class="space-y-1.5 pt-1">
                ${(data.sources && data.sources.libgen || []).map(m => `
                    <div class="flex items-center justify-between text-[11px]">
                        <a href="${m.url}" target="_blank" rel="noopener" class="text-purple-300 hover:underline font-mono">${escapeHtml(m.url)}</a>
                        <span class="text-slate-400 font-mono">${m.latency_ms} ms</span>
                    </div>
                `).join('') || '<div class="text-slate-500 italic">Ingen aktive spejle fundet</div>'}
            </div>
        `;
        mirrorsContainer.appendChild(libgenDiv);

        // Anna's Archive section
        const annasDiv = document.createElement('div');
        annasDiv.className = 'p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-2';
        annasDiv.innerHTML = `
            <div class="flex items-center justify-between">
                <strong class="text-white text-xs font-semibold">🔍 Anna's Archive (Sekundær)</strong>
                <span class="text-[10px] text-blue-400 bg-blue-500/10 px-1.5 py-0.5 rounded border border-blue-500/20">Fallback</span>
            </div>
            <div class="space-y-1.5 pt-1">
                ${(data.sources && data.sources.annas || []).map(m => `
                    <div class="flex items-center justify-between text-[11px]">
                        <a href="${m.url}" target="_blank" rel="noopener" class="text-purple-300 hover:underline font-mono">${escapeHtml(m.url)}</a>
                        <div class="flex items-center gap-1.5">
                            ${m.ddos_guard ? '<span class="text-[9px] bg-amber-500/15 text-amber-300 border border-amber-500/30 px-1 rounded">DDG</span>' : ''}
                            <span class="text-slate-400 font-mono">${m.latency_ms} ms</span>
                        </div>
                    </div>
                `).join('') || '<div class="text-slate-500 italic">Ingen aktive spejle fundet</div>'}
            </div>
        `;
        mirrorsContainer.appendChild(annasDiv);
    } catch (err) {
        if (mirrorsContainer) mirrorsContainer.innerHTML = `<div class="p-3 text-red-400 col-span-2">Fejl ved hentning af spejle: ${escapeHtml(err.message)}</div>`;
        if (mirrorCacheBadge) mirrorCacheBadge.textContent = 'Fejl';
    }
}

window.runBookSearch = async function (query) {
    const bookSearchInput = document.getElementById('book-search-input');
    const bookSearchSubmitBtn = document.getElementById('book-search-submit-btn');
    const bookSearchResultsBox = document.getElementById('book-search-results-box');
    const bookSearchCount = document.getElementById('book-search-count');
    const bookSearchLatency = document.getElementById('book-search-latency');
    const bookSearchResultsList = document.getElementById('book-search-results-list');

    if (bookSearchInput) bookSearchInput.value = query;
    if (!query || !query.trim()) return;

    const categorySelect = document.getElementById('book-search-category');
    const category = categorySelect ? categorySelect.value : 'all_books';
    const langSelect = document.getElementById('book-search-language');
    const lang = langSelect ? langSelect.value : 'all';

    if (bookSearchResultsBox) bookSearchResultsBox.classList.remove('hidden');
    if (bookSearchCount) bookSearchCount.textContent = `Søger efter "${query}"...`;
    if (bookSearchLatency) bookSearchLatency.textContent = '';
    if (bookSearchResultsList) bookSearchResultsList.innerHTML = '<div class="p-4 text-center text-slate-400 italic">Søger over aktive spejle (Libgen / Anna)...</div>';
    if (bookSearchSubmitBtn) bookSearchSubmitBtn.disabled = true;

    const t0 = performance.now();
    try {
        let searchUrl = `/api/v1/store/search?q=${encodeURIComponent(query.trim())}&category=${encodeURIComponent(category)}`;
        if (lang && lang !== 'all') {
            searchUrl += `&lang=${encodeURIComponent(lang)}`;
        }
        const res = await fetch(searchUrl);
        if (!res.ok) throw new Error('Søgning fejlede på serveren');
        const data = await res.json();
        const latency = Math.round(performance.now() - t0);

        const count = (data.results || []).length;
        if (bookSearchCount) bookSearchCount.textContent = `Fundet ${count} ${count === 1 ? 'bog' : 'bøger'} for "${escapeHtml(data.query)}"`;
        if (bookSearchLatency) bookSearchLatency.textContent = `⚡ ${latency} ms`;

        if (count === 0) {
            if (bookSearchResultsList) bookSearchResultsList.innerHTML = '<div class="p-4 text-center text-slate-500 italic">Ingen bøger fundet for denne søgning.</div>';
            return;
        }

        if (bookSearchResultsList) {
            bookSearchResultsList.innerHTML = data.results.map(b => `
                <div class="p-3.5 hover:bg-slate-800/50 transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div class="space-y-1 max-w-2xl">
                        <div class="flex items-center gap-2">
                            <span class="font-bold text-white text-xs">${escapeHtml(b.title)}</span>
                            ${b.year ? `<span class="text-slate-400 text-[11px]">(${escapeHtml(b.year)})</span>` : ''}
                        </div>
                        <div class="flex flex-wrap items-center gap-x-3 text-[11px] text-slate-400">
                            <span>Forfatter: <strong class="text-slate-300">${escapeHtml(b.author)}</strong></span>
                            ${b.language ? `<span>Sprog: <span class="text-slate-300">${escapeHtml(b.language)}</span></span>` : ''}
                            ${b.size ? `<span>Størrelse: <span class="text-slate-300">${escapeHtml(b.size)}</span></span>` : ''}
                        </div>
                    </div>
                    <div class="flex items-center gap-2 self-start sm:self-center shrink-0">
                        <span class="px-2 py-0.5 rounded font-mono text-[10px] font-semibold ${b.format === 'epub' ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30' : 'bg-slate-800 text-slate-300 border border-slate-700'}">${(b.format || 'epub').toUpperCase()}</span>
                        ${b.download_page ? `<a href="${b.download_page}" target="_blank" rel="noopener" class="px-2.5 py-1 bg-purple-500/15 hover:bg-purple-500/25 text-purple-300 border border-purple-500/30 rounded text-[11px] font-medium transition-colors">Hent</a>` : ''}
                    </div>
                </div>
            `).join('');
        }
    } catch (err) {
        if (bookSearchCount) bookSearchCount.textContent = 'Fejl under søgning';
        if (bookSearchResultsList) bookSearchResultsList.innerHTML = `<div class="p-4 text-red-400">Fejl: ${escapeHtml(err.message)}</div>`;
    } finally {
        if (bookSearchSubmitBtn) bookSearchSubmitBtn.disabled = false;
    }
};

function updateOpdsUrl() {
    const opdsFeedUrlInput = document.getElementById('opds-feed-url-input');
    const opdsApiKeyInput = document.getElementById('opds-api-key-input');
    if (!opdsFeedUrlInput) return;

    const hostRadio = document.querySelector('input[name="opds-host-mode"]:checked');
    const baseHost = (hostRadio ? hostRadio.value : null) || 'https://ordpresse.seame.click';
    const keyVal = opdsApiKeyInput ? opdsApiKeyInput.value.trim() : '';

    if (keyVal) {
        opdsFeedUrlInput.value = `${baseHost}/opds?api_key=${encodeURIComponent(keyVal)}`;
        try {
            localStorage.setItem('ordpresse_api_key', keyVal);
        } catch (e) { }
    } else {
        opdsFeedUrlInput.value = `${baseHost}/opds`;
    }
}

async function loadBooksArchive() {
    const booksTbody = document.getElementById('books-tbody');
    const booksCountBadge = document.getElementById('books-count-badge');
    const booksStorageStat = document.getElementById('books-storage-stat');
    if (!booksTbody) return;

    try {
        const res = await fetch('/api/books');
        if (res.status === 401) {
            booksTbody.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-amber-400">Log ind påkrævet for at se bogarkivet.</td></tr>';
            if (booksCountBadge) booksCountBadge.textContent = 'Låst';
            return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const books = await res.json();

        if (!Array.isArray(books) || books.length === 0) {
            booksTbody.innerHTML = '<tr><td colspan="6" class="py-8 text-center text-slate-500 italic">Ingen bøger i arkivet endnu. Hent en bog via Anna Fetch på din e-bogslæser eller søgefeltet ovenfor!</td></tr>';
            if (booksCountBadge) booksCountBadge.textContent = '0 bøger';
            if (booksStorageStat) booksStorageStat.textContent = '';
            return;
        }

        if (booksCountBadge) booksCountBadge.textContent = `${books.length} ${books.length === 1 ? 'bog' : 'bøger'}`;

        const totalBytes = books.reduce((sum, b) => sum + (b.size_bytes || 0), 0);
        const totalMb = (totalBytes / (1024 * 1024)).toFixed(1);
        if (booksStorageStat) booksStorageStat.textContent = `Samlet størrelse: ${totalMb} MB`;

        booksTbody.innerHTML = books.map(b => {
            const sizeStr = b.size_bytes ? (b.size_bytes > 1024 * 1024 ? `${(b.size_bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.round(b.size_bytes / 1024)} KB`) : '-';
            const fmt = (b.format || 'epub').toUpperCase();

            let dateStr = '-';
            if (b.downloaded_at) {
                try {
                    const d = new Date(b.downloaded_at);
                    dateStr = d.toLocaleDateString('da-DK', { day: '2-digit', month: 'short', year: 'numeric' });
                } catch (e) {
                    dateStr = b.downloaded_at;
                }
            }

            return `
                <tr class="hover:bg-slate-900/40 transition-colors">
                    <td class="py-3 px-4">
                        <div class="font-bold text-white">${escapeHtml(b.title)}</div>
                        <div class="text-[11px] text-slate-400">${escapeHtml(b.author)}</div>
                    </td>
                    <td class="py-3 px-4 font-mono text-[11px] text-amber-300/90 break-all max-w-xs">
                        ${escapeHtml(b.filename)}
                    </td>
                    <td class="py-3 px-4">
                        <span class="px-2 py-0.5 rounded font-mono text-[10px] font-semibold ${fmt === 'EPUB' ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30' : 'bg-slate-800 text-slate-300 border border-slate-700'}">${fmt}</span>
                    </td>
                    <td class="py-3 px-4 text-slate-300 font-mono text-[11px]">${sizeStr}</td>
                    <td class="py-3 px-4 text-slate-400 text-[11px]">${dateStr}</td>
                    <td class="py-3 px-4 text-right">
                        <div class="flex items-center justify-end gap-2">
                            <a href="/opds/download/${escapeHtml(b.md5)}" class="px-2.5 py-1 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 rounded-lg text-[11px] font-medium transition-colors flex items-center gap-1 active:scale-95" title="Hent fil">
                                <span>⬇</span> <span>Hent</span>
                            </a>
                            <button type="button" onclick="deleteArchivedBook('${escapeHtml(b.md5)}', '${escapeHtml(b.title)}')" class="px-2.5 py-1 bg-slate-800 hover:bg-red-500/20 text-slate-400 hover:text-red-300 border border-slate-700 hover:border-red-500/30 rounded-lg text-[11px] font-medium transition-colors active:scale-95 cursor-pointer" title="Slet fra arkiv">
                                <span>🗑️</span>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        console.error("Fejl ved hentning af bøger:", err);
        booksTbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-red-400">Kunne ikke hente bogarkiv: ${escapeHtml(err.message)}</td></tr>`;
        if (booksCountBadge) booksCountBadge.textContent = 'Fejl';
    }
}

window.deleteArchivedBook = async function (md5, title) {
    if (!confirm(`Er du sikker på, at du vil slette "${title}" fra det centrale bogarkiv?`)) return;
    try {
        const res = await fetch(`/api/books/${encodeURIComponent(md5)}`, { method: 'DELETE' });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || `HTTP ${res.status}`);
        }
        loadBooksArchive();
    } catch (err) {
        alert(`Fejl ved sletning af bog: ${err.message}`);
    }
};

function initStore() {
    const refreshMirrorsBtn = document.getElementById('refresh-mirrors-btn');
    const refreshBooksBtn = document.getElementById('refresh-books-btn');
    const bookSearchSubmitBtn = document.getElementById('book-search-submit-btn');
    const bookSearchInput = document.getElementById('book-search-input');
    const opdsApiKeyInput = document.getElementById('opds-api-key-input');
    const copyOpdsUrlBtn = document.getElementById('copy-opds-url-btn');
    const copyOpdsLabel = document.getElementById('copy-opds-label');
    const downloadOpdsJsonBtn = document.getElementById('download-opds-json-btn');

    if (refreshMirrorsBtn) {
        refreshMirrorsBtn.addEventListener('click', () => loadStoreMirrors(true));
    }

    if (refreshBooksBtn) {
        refreshBooksBtn.addEventListener('click', () => loadBooksArchive());
    }

    if (bookSearchSubmitBtn && bookSearchInput) {
        bookSearchSubmitBtn.addEventListener('click', () => runBookSearch(bookSearchInput.value));
        bookSearchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') runBookSearch(bookSearchInput.value);
        });
    }

    document.querySelectorAll('input[name="opds-host-mode"]').forEach(r => {
        r.addEventListener('change', updateOpdsUrl);
    });

    if (downloadOpdsJsonBtn) {
        downloadOpdsJsonBtn.addEventListener('click', () => {
            const hostRadio = document.querySelector('input[name="opds-host-mode"]:checked');
            const baseHost = (hostRadio ? hostRadio.value : null) || 'https://ordpresse.seame.click';
            const keyVal = opdsApiKeyInput ? opdsApiKeyInput.value.trim() : '';
            const url = keyVal ? `${baseHost}/opds?api_key=${encodeURIComponent(keyVal)}` : `${baseHost}/opds`;

            const opdsConfig = [
                {
                    "name": "Ordpresse",
                    "url": url,
                    "username": "",
                    "password": "",
                    "filenameFormat": "title_author"
                }
            ];

            const blob = new Blob([JSON.stringify(opdsConfig, null, 2)], { type: 'application/json' });
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            a.download = 'opds.json';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(blobUrl);
        });
    }

    if (opdsApiKeyInput) {
        try {
            const savedKey = localStorage.getItem('ordpresse_api_key');
            if (savedKey) {
                opdsApiKeyInput.value = savedKey;
            }
        } catch (e) { }
        updateOpdsUrl();
        opdsApiKeyInput.addEventListener('input', updateOpdsUrl);
    }

    if (copyOpdsUrlBtn) {
        copyOpdsUrlBtn.addEventListener('click', async () => {
            const opdsFeedUrlInput = document.getElementById('opds-feed-url-input');
            if (!opdsFeedUrlInput) return;
            const text = opdsFeedUrlInput.value;
            try {
                await navigator.clipboard.writeText(text);
                if (copyOpdsLabel) copyOpdsLabel.textContent = 'Kopieret! ✓';
                setTimeout(() => {
                    if (copyOpdsLabel) copyOpdsLabel.textContent = 'Kopier URL';
                }, 2000);
            } catch (e) {
                opdsFeedUrlInput.select();
                document.execCommand('copy');
                if (copyOpdsLabel) copyOpdsLabel.textContent = 'Kopieret! ✓';
                setTimeout(() => {
                    if (copyOpdsLabel) copyOpdsLabel.textContent = 'Kopier URL';
                }, 2000);
            }
        });
    }

    initEpubUpload();
    initV16Panels();

    // Initial load
    loadStoreMirrors();
    loadBooksArchive();
    loadSavedArticles();
    loadUdklipData();
}

async function loadSavedArticles() {
    const listEl = document.getElementById('saved-articles-list');
    if (!listEl) return;
    try {
        const res = await fetch('/api/v1/articles');
        if (!res.ok) return;
        const data = await res.json();
        const items = data.articles || [];
        if (items.length === 0) {
            listEl.innerHTML = '<div class="py-3 text-slate-500 italic">Ingen gemte artikler endnu.</div>';
            return;
        }
        listEl.innerHTML = items.map(a => `
            <div class="py-2.5 flex items-center justify-between gap-2">
                <div class="min-w-0">
                    <div class="font-semibold text-white truncate">${escapeHtml(a.title)}</div>
                    <div class="text-[11px] text-slate-400">${escapeHtml(a.author)} · ${Math.max(1, Math.round((a.size_bytes || 0) / 1024))} KB</div>
                </div>
                <div class="flex items-center gap-1.5 shrink-0">
                    <a href="/opds/articles/download/${escapeHtml(a.md5)}" class="px-2 py-1 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 rounded text-[11px]">EPUB</a>
                    <button type="button" onclick="deleteSavedArticle('${escapeHtml(a.md5)}')" class="px-2 py-1 bg-slate-800 hover:bg-red-500/20 text-slate-400 hover:text-red-300 border border-slate-700 rounded text-[11px] cursor-pointer">×</button>
                </div>
            </div>
        `).join('');
    } catch (e) { }
}

window.deleteSavedArticle = async function (md5) {
    try {
        await fetch(`/api/v1/articles/${encodeURIComponent(md5)}`, { method: 'DELETE' });
        loadSavedArticles();
    } catch (e) { }
};

async function loadUdklipData() {
    const listEl = document.getElementById('udklip-highlights-list');
    const badgeEl = document.getElementById('udklip-count-badge');
    if (!listEl) return;
    try {
        const res = await fetch('/api/v1/udklip');
        if (!res.ok) return;
        const data = await res.json();
        const highlights = data.highlights || [];
        const vocab = data.vocab_words || [];
        if (badgeEl) badgeEl.textContent = `${highlights.length} citater · ${vocab.length} gloser`;

        if (highlights.length === 0 && vocab.length === 0) {
            listEl.innerHTML = '<div class="py-3 text-slate-500 italic">Ingen synkroniserede citater eller gloser endnu.</div>';
            return;
        }
        const parts = [];
        highlights.slice(0, 20).forEach(h => {
            parts.push(`
                <div class="py-2.5 space-y-1">
                    <div class="text-slate-200">»${escapeHtml(h.quote_text)}«</div>
                    ${h.note ? `<div class="text-[11px] text-amber-300">Note: ${escapeHtml(h.note)}</div>` : ''}
                    <div class="text-[10px] text-slate-500">${escapeHtml(h.book_title || 'Ukendt bog')}${h.chapter ? ' · ' + escapeHtml(h.chapter) : ''}</div>
                </div>
            `);
        });
        vocab.slice(0, 20).forEach(v => {
            parts.push(`
                <div class="py-2 space-y-0.5">
                    <div class="font-semibold text-cyan-300">${escapeHtml(v.word)} <span class="text-[10px] font-normal text-slate-500">(${escapeHtml(v.book_title || 'Glosebog')})</span></div>
                    ${v.context ? `<div class="text-[11px] text-slate-400">${escapeHtml(v.context)}</div>` : ''}
                </div>
            `);
        });
        listEl.innerHTML = parts.join('');
    } catch (e) { }
}

function initV16Panels() {
    const saveBtn = document.getElementById('article-save-btn');
    const refreshBtn = document.getElementById('refresh-v16-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadSavedArticles();
            loadUdklipData();
        });
    }
    if (saveBtn) {
        saveBtn.addEventListener('click', async () => {
            const url = (document.getElementById('article-url-input')?.value || '').trim();
            const title = (document.getElementById('article-title-input')?.value || '').trim();
            const author = (document.getElementById('article-author-input')?.value || '').trim();
            const rawContent = (document.getElementById('article-raw-input')?.value || '').trim();
            const statusEl = document.getElementById('article-save-status');
            if (!url && !rawContent) {
                if (statusEl) statusEl.textContent = 'Indtast URL eller tekst.';
                return;
            }
            saveBtn.disabled = true;
            if (statusEl) statusEl.textContent = 'Konverterer og vasker EPUB...';
            try {
                const res = await fetch('/api/v1/articles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, title, author, raw_content: rawContent }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
                if (statusEl) statusEl.textContent = `Gemt: ${data.article?.title || 'EPUB klar'}`;
                if (document.getElementById('article-url-input')) document.getElementById('article-url-input').value = '';
                if (document.getElementById('article-raw-input')) document.getElementById('article-raw-input').value = '';
                loadSavedArticles();
            } catch (err) {
                if (statusEl) statusEl.textContent = `Fejl: ${err.message}`;
            } finally {
                saveBtn.disabled = false;
            }
        });
    }
}

function initEpubUpload() {
    const dropZone = document.getElementById('epub-drop-zone');
    const fileInput = document.getElementById('epub-file-input');
    const washCheckbox = document.getElementById('epub-wash-checkbox');
    const statusBox = document.getElementById('epub-upload-status');

    if (!dropZone || !fileInput) return;

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('border-cyan-400', 'bg-cyan-500/10');
    });

    dropZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dropZone.classList.remove('border-cyan-400', 'bg-cyan-500/10');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('border-cyan-400', 'bg-cyan-500/10');
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleEpubUpload(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files && fileInput.files.length > 0) {
            handleEpubUpload(fileInput.files[0]);
            fileInput.value = '';
        }
    });

    async function handleEpubUpload(file) {
        if (!file.name.toLowerCase().endsWith('.epub')) {
            alert('Vælg venligst en gyldig .epub fil.');
            return;
        }

        const wash = washCheckbox ? washCheckbox.checked : true;
        statusBox.classList.remove('hidden');
        statusBox.innerHTML = `
            <div class="flex items-center gap-3 text-cyan-300">
                <span class="animate-spin text-lg">↻</span>
                <div>
                    <div class="font-bold">Uploader "${escapeHtml(file.name)}"...</div>
                    <div class="text-[11px] text-slate-400">
                        ${wash ? 'Kører Bogvaskeri v2.0: DOM OCR-heling, typografi, CSS/billed-rens og offline leksikoncache...' : 'Gemmer i arkivet...'}
                    </div>
                </div>
            </div>
        `;

        const formData = new FormData();
        formData.append('file', file);
        formData.append('wash', wash ? 'true' : 'false');

        try {
            const res = await fetch('/api/books/upload', {
                method: 'POST',
                body: formData,
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `HTTP ${res.status}`);
            }

            const data = await res.json();
            const b = data.book || {};
            const l = b.laundry || {};

            let statsHtml = '';
            if (b.washed && l) {
                statsHtml = `
                    <div class="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] text-slate-300 bg-slate-950/60 p-2.5 rounded-lg border border-slate-800">
                        <div>Helede afsnit: <strong class="text-cyan-400 font-mono">${l.paragraphs_healed || 0}</strong></div>
                        <div>Indlejrede ord: <strong class="text-amber-400 font-mono">${l.prewarmed_words || 0}</strong></div>
                        <div>Sprog: <strong class="text-emerald-400 font-mono">${(l.detected_language || 'da').toUpperCase()}</strong></div>
                        <div>Tid: <strong class="text-slate-400 font-mono">${l.processing_ms || 0} ms</strong></div>
                    </div>
                `;
            }

            statusBox.innerHTML = `
                <div class="space-y-1.5 text-emerald-400">
                    <div class="flex items-center gap-2 font-bold text-sm">
                        <span>✓</span> <span>Bogen "${escapeHtml(b.title || file.name)}" er klar!</span>
                    </div>
                    <p class="text-[11px] text-slate-300">
                        Gemt som <code class="text-amber-300 font-mono">${escapeHtml(b.filename || '')}</code> i arkivet. Den kan nu hentes med det samme på din Kindle eller CrossPoint via OPDS!
                    </p>
                    ${statsHtml}
                </div>
            `;

            // Reload books table
            loadBooksArchive();
        } catch (err) {
            statusBox.innerHTML = `
                <div class="text-red-400 space-y-1">
                    <div class="font-bold flex items-center gap-1.5">
                        <span>⚠️</span> <span>Fejl under upload / bogvask</span>
                    </div>
                    <p class="text-[11px]">${escapeHtml(err.message)}</p>
                </div>
            `;
        }
    }
}

document.addEventListener('DOMContentLoaded', initStore);

