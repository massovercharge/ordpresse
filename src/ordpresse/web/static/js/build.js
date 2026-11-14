/**
 * Build & Compiler Pipeline Controller for Den Danske Ordpresse
 */

let currentBuildId = null;
let currentEvtSource = null;
let selectedBatchLimit = null; // null means 'all'
let missingWordsPollTimer = null;
let lastLogText = null;
let lastLogElement = null;

function updateOpenOrdnetLinkHref() {
    const openOrdnetLink = document.getElementById('open-ordnet-link');
    if (!openOrdnetLink || !currentBuildId) return;
    const limitParam = selectedBatchLimit ? `&limit=${selectedBatchLimit}` : '';
    openOrdnetLink.href = `https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id=${encodeURIComponent(currentBuildId)}&ordpresse_origin=${encodeURIComponent(window.location.origin)}${limitParam}`;
    const btnLabel = document.getElementById('open-ordnet-btn-label');
    if (btnLabel) {
        btnLabel.textContent = selectedBatchLimit ? `⚡ 1. Åbn ordnet.dk (Hent top ${selectedBatchLimit} ord) ↗` : `⚡ 1. Åbn ordnet.dk (Hent alle ord) ↗`;
    }
    const idCode = document.getElementById('active-build-id-code');
    if (idCode) {
        idCode.textContent = currentBuildId;
    }
    try {
        window.location.hash = `ordpresse_id=${encodeURIComponent(currentBuildId)}${limitParam}`;
    } catch (e) { }
}

async function skipQueue(buildId) {
    if (!buildId) return;
    const skipQueueBtn = document.getElementById('skip-queue-btn');
    const logBox = document.getElementById('log-box');
    if (skipQueueBtn) {
        skipQueueBtn.disabled = true;
        skipQueueBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }
    if (logBox) {
        const logEntry = document.createElement('div');
        logEntry.className = 'text-amber-400 font-semibold';
        logEntry.textContent = `[${new Date().toLocaleTimeString()}] Springer resterende kø over (${buildId})...`;
        logBox.appendChild(logEntry);
        logBox.scrollTop = logBox.scrollHeight;
    }
    try {
        await fetch(`/api/build/${buildId}/skip-queue`, { method: 'POST' });
    } catch (err) {
        console.error("Kunne ikke springe kø over:", err);
    }
}

async function startServerScrape(buildId) {
    if (!buildId) return;
    const startServerScrapeBtn = document.getElementById('start-server-scrape-btn');
    const logBox = document.getElementById('log-box');
    if (startServerScrapeBtn) {
        startServerScrapeBtn.disabled = true;
        startServerScrapeBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }
    if (logBox) {
        const logEntry = document.createElement('div');
        logEntry.className = 'text-amber-400 font-semibold';
        logEntry.textContent = `[${new Date().toLocaleTimeString()}] Skifter til automatisk server-kø (${buildId})...`;
        logBox.appendChild(logEntry);
        logBox.scrollTop = logBox.scrollHeight;
    }
    try {
        await fetch(`/api/build/${buildId}/start-server-scrape`, { method: 'POST' });
    } catch (err) {
        console.error("Kunne ikke starte server-kø:", err);
    }
}

function stopMissingWordsPolling() {
    if (missingWordsPollTimer) {
        clearInterval(missingWordsPollTimer);
        missingWordsPollTimer = null;
    }
}

function startMissingWordsPolling(bid) {
    stopMissingWordsPolling();
    missingWordsPollTimer = setInterval(() => {
        if (currentBuildId && currentBuildId === bid) {
            updateMissingWordsCount(bid);
        }
    }, 2000);
}

async function updateMissingWordsCount(bid) {
    if (!bid) return;
    const missingWordsBadge = document.getElementById('missing-words-badge');
    const peerHelperPanel = document.getElementById('peer-helper-panel');
    try {
        const res = await fetch(`/api/build/${bid}/missing-words`);
        if (res.ok) {
            const data = await res.json();
            if (missingWordsBadge) {
                if (data.resolved_count !== undefined && data.resolved_count > 0) {
                    missingWordsBadge.textContent = `${data.resolved_count} ord hentet`;
                } else if (data.total_pending !== undefined && data.total_pending > 0) {
                    missingWordsBadge.textContent = `${data.total_pending} ord i kø`;
                } else {
                    missingWordsBadge.textContent = 'Klar til opslag';
                }
            }
            if (peerHelperPanel && currentBuildId) {
                peerHelperPanel.classList.remove('hidden');
            }
        }
    } catch (e) { }
}

function appendLog(msg, colorClass = null) {
    if (!msg) return;
    const logBox = document.getElementById('log-box');
    if (!logBox) return;

    const isLynHenter = msg.startsWith('🚀 Lyn-Henter aktiv:') || msg.startsWith('⚡ Lyn-Henter aktiv:');
    const isWaiting = msg.includes('Venter på Lyn-Henter');

    // Deduplicate identical or consecutive waiting messages
    if (msg === lastLogText) {
        return;
    }
    if (isWaiting && lastLogText && lastLogText.includes('Venter på Lyn-Henter')) {
        return;
    }
    // Update existing Lyn-Henter progress line if active
    if (isLynHenter && lastLogElement && (lastLogText.startsWith('🚀 Lyn-Henter aktiv:') || lastLogText.startsWith('⚡ Lyn-Henter aktiv:'))) {
        lastLogElement.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
        lastLogText = msg;
        logBox.scrollTop = logBox.scrollHeight;
        return;
    }

    const logEntry = document.createElement('div');
    if (colorClass) {
        logEntry.className = colorClass;
    }
    logEntry.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    logBox.appendChild(logEntry);
    logBox.scrollTop = logBox.scrollHeight;
    lastLogText = msg;
    lastLogElement = logEntry;
}

function updateTelemetry(tel) {
    if (!tel) return;
    const fmt = (n) => (typeof n === 'number') ? n.toLocaleString('da-DK') : n;

    if (tel.books_count !== undefined && tel.unique_words !== undefined) {
        const bVal = document.getElementById('stat-val-books');
        const bSub = document.getElementById('stat-sub-books');
        if (bVal) bVal.textContent = `${tel.books_count} ${tel.books_count === 1 ? 'bog' : 'bøger'}`;
        if (bSub) bSub.textContent = `${fmt(tel.unique_words)} unikke ord`;
    } else if (tel.unique_words !== undefined) {
        const bVal = document.getElementById('stat-val-books');
        if (bVal) bVal.textContent = `${fmt(tel.unique_words)} ord`;
    }

    if (tel.local_covered !== undefined) {
        const lVal = document.getElementById('stat-val-local');
        const lSub = document.getElementById('stat-sub-local');
        if (lVal) lVal.textContent = `${fmt(tel.local_covered)} ord`;
        if (lSub) lSub.textContent = `${fmt(tel.cor_matches || 0)} COR · ${fmt(tel.ods_cache_matches || 0)} ODS`;
    }

    if (tel.priority_uncached !== undefined) {
        const aVal = document.getElementById('stat-val-archaic');
        const aSub = document.getElementById('stat-sub-archaic');
        if (aVal) aVal.textContent = `${fmt(tel.priority_uncached)} ord`;
        if (aSub) aSub.textContent = tel.new_historical ? `af ${fmt(tel.new_historical)} fundne` : 'kan beriges';
    } else if (tel.resolved !== undefined && tel.remaining !== undefined) {
        const aVal = document.getElementById('stat-val-archaic');
        const aSub = document.getElementById('stat-sub-archaic');
        if (aVal) aVal.textContent = `${fmt(tel.resolved)} hentet`;
        if (aSub) aSub.textContent = `${fmt(tel.remaining)} i kø`;
    }

    if (tel.idx_entries !== undefined) {
        const pVal = document.getElementById('stat-val-package');
        const pSub = document.getElementById('stat-sub-package');
        if (pVal) pVal.textContent = `${fmt(tel.idx_entries)} opslag`;
        if (pSub) pSub.textContent = tel.syn_entries ? `${fmt(tel.syn_entries)} bøjninger` : '.idx klar';
    }
}

function updateFileList() {
    const epubInput = document.getElementById('epub-input');
    const fileCountLabel = document.getElementById('file-count-label');
    const selectedFilesList = document.getElementById('selected-files-list');
    if (!epubInput || !fileCountLabel || !selectedFilesList) return;

    selectedFilesList.innerHTML = '';
    const files = epubInput.files;
    if (!files || files.length === 0) {
        fileCountLabel.textContent = 'Ingen filer valgt endnu';
        return;
    }
    fileCountLabel.textContent = `${files.length} EPUB-fil${files.length > 1 ? 'er' : ''} valgt`;
    const pillContainer = document.createElement('div');
    pillContainer.className = 'flex flex-wrap gap-2 pt-1';
    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const pill = document.createElement('div');
        pill.className = 'flex items-center gap-2 bg-slate-900 border border-slate-700/80 px-3 py-1.5 rounded-lg text-xs text-slate-200 shadow-sm';
        const sizeStr = file.size > 1024 * 1024
            ? (file.size / (1024 * 1024)).toFixed(1) + ' MB'
            : Math.round(file.size / 1024) + ' KB';
        pill.innerHTML = `
            <span class="text-amber-400 text-sm">📚</span>
            <span class="truncate max-w-[200px]" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
            <span class="text-slate-500 text-[11px] font-mono">(${sizeStr})</span>
            <button type="button" class="text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 rounded px-1 transition-colors font-bold text-sm leading-none ml-1 cursor-pointer" title="Fjern fil" data-index="${i}">×</button>
        `;
        const removeBtn = pill.querySelector('button');
        removeBtn.addEventListener('click', (ev) => {
            ev.stopPropagation();
            removeEpubFile(i);
        });
        pillContainer.appendChild(pill);
    }
    selectedFilesList.appendChild(pillContainer);
}

function removeEpubFile(indexToRemove) {
    const epubInput = document.getElementById('epub-input');
    if (!epubInput) return;
    const dt = new DataTransfer();
    const files = epubInput.files;
    for (let i = 0; i < files.length; i++) {
        if (i !== indexToRemove) {
            dt.items.add(files[i]);
        }
    }
    epubInput.files = dt.files;
    updateFileList();
}

function toggleDictionaryScope() {
    const isFull = document.querySelector('input[name="full_dictionary"]:checked')?.value === 'true';
    const stepEpubUpload = document.getElementById('step-epub-upload');
    const stepCustomWords = document.getElementById('step-custom-words');
    const fullDictInfoBox = document.getElementById('full-dict-info-box');

    if (isFull) {
        if (stepEpubUpload) stepEpubUpload.classList.add('hidden');
        if (stepCustomWords) stepCustomWords.classList.add('hidden');
        if (fullDictInfoBox) fullDictInfoBox.classList.remove('hidden');
    } else {
        if (stepEpubUpload) stepEpubUpload.classList.remove('hidden');
        if (stepCustomWords) stepCustomWords.classList.remove('hidden');
        if (fullDictInfoBox) fullDictInfoBox.classList.add('hidden');
    }
}

function initBuild() {
    const dropzone = document.getElementById('dropzone');
    const epubInput = document.getElementById('epub-input');
    const buildForm = document.getElementById('build-form');
    const submitBtn = document.getElementById('submit-btn');
    const progressCard = document.getElementById('progress-card');
    const progressBarFill = document.getElementById('progress-bar-fill');
    const progressPercent = document.getElementById('progress-percent');
    const progressMessage = document.getElementById('progress-message');
    const progressStatusTitle = document.getElementById('progress-status-title');
    const statusSpinner = document.getElementById('status-spinner');
    const logBox = document.getElementById('log-box');
    const cancelBuildBtn = document.getElementById('cancel-build-btn');
    const cancelledBanner = document.getElementById('cancelled-banner');
    const completionBanner = document.getElementById('completion-banner');
    const completionStats = document.getElementById('completion-stats');
    const downloadBtn = document.getElementById('download-btn');
    const loadMobergBtn = document.getElementById('load-moberg-btn');
    const customWordsTextarea = document.getElementById('custom_words');
    const peerHelperPanel = document.getElementById('peer-helper-panel');
    const skipQueueBtn = document.getElementById('skip-queue-btn');
    const startServerScrapeBtn = document.getElementById('start-server-scrape-btn');
    const fullDictRadios = document.querySelectorAll('input[name="full_dictionary"]');

    // Scope toggle
    fullDictRadios.forEach(r => r.addEventListener('change', toggleDictionaryScope));
    toggleDictionaryScope();

    // Dropzone
    if (dropzone && epubInput) {
        dropzone.addEventListener('click', () => epubInput.click());
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('border-amber-400', 'bg-slate-900/80');
        });
        dropzone.addEventListener('dragleave', () => {
            dropzone.classList.remove('border-amber-400', 'bg-slate-900/80');
        });
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('border-amber-400', 'bg-slate-900/80');
            if (e.dataTransfer.files.length > 0) {
                epubInput.files = e.dataTransfer.files;
                updateFileList();
            }
        });
        epubInput.addEventListener('change', updateFileList);
    }

    // Moberg shortcut
    if (loadMobergBtn && customWordsTextarea) {
        loadMobergBtn.addEventListener('click', () => {
            customWordsTextarea.value = "lægdslemmer, jordebog, bissekræmmer, værkbrudne, banco";
        });
    }

    // Batch chips
    document.querySelectorAll('.batch-chip').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.batch-chip').forEach(b => {
                b.classList.remove('bg-amber-500/20', 'text-amber-300', 'border-amber-500/50', 'active');
                b.classList.add('bg-slate-800', 'text-slate-300', 'border-slate-700');
            });
            btn.classList.remove('bg-slate-800', 'text-slate-300', 'border-slate-700');
            btn.classList.add('bg-amber-500/20', 'text-amber-300', 'border-amber-500/50', 'active');

            const val = btn.dataset.batch;
            selectedBatchLimit = (val === 'all') ? null : parseInt(val, 10);
            const selLabel = document.getElementById('batch-selection-label');
            if (selLabel) {
                selLabel.textContent = selectedBatchLimit ? `Top ${selectedBatchLimit} ord` : 'Alle ord';
            }
            updateOpenOrdnetLinkHref();
        });
    });

    if (startServerScrapeBtn) {
        startServerScrapeBtn.addEventListener('click', () => startServerScrape(currentBuildId));
    }

    if (skipQueueBtn) {
        skipQueueBtn.addEventListener('click', () => skipQueue(currentBuildId));
    }

    if (cancelBuildBtn) {
        cancelBuildBtn.addEventListener('click', async () => {
            if (!currentBuildId) return;
            stopMissingWordsPolling();
            cancelBuildBtn.disabled = true;
            cancelBuildBtn.classList.add('opacity-50', 'cursor-not-allowed');
            if (progressStatusTitle) progressStatusTitle.textContent = 'Afbryder byggeproces...';
            if (logBox) {
                const logEntry = document.createElement('div');
                logEntry.className = 'text-rose-400 font-semibold';
                logEntry.textContent = `[${new Date().toLocaleTimeString()}] Afbryder byggejob (${currentBuildId})...`;
                logBox.appendChild(logEntry);
                logBox.scrollTop = logBox.scrollHeight;
            }
            if (peerHelperPanel) peerHelperPanel.classList.add('hidden');
            try {
                await fetch(`/api/build/cancel/${currentBuildId}`, { method: 'POST' });
            } catch (err) {
                console.error("Kunne ikke sende afbrydelsesanmodning:", err);
            }
        });
    }

    // Dynamic Bookmarklet code
    const bookmarkletBtn = document.getElementById('bookmarklet-btn');
    if (bookmarkletBtn) {
        bookmarkletBtn.href = "javascript:(async function() { const m = location.hash.match(/ordpresse_id=([^&]+)/); const o = location.hash.match(/ordpresse_origin=([^&]+)/); const l = location.hash.match(/limit=(\\d+)/); const bid = m ? m[1] : prompt(\"Indtast Ordpresse Build-ID:\"); const host = o ? decodeURIComponent(o[1]) : (window.location.origin || \"https://ordpresse.seame.click\"); if (!bid) return; if (!location.hostname.includes(\"ordnet.dk\")) { location.href = \"https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id=\" + bid + \"&ordpresse_origin=\" + encodeURIComponent(location.origin) + (l ? \"&limit=\" + l[1] : \"\"); return; } const box = document.createElement(\"div\"); box.style = \"position:fixed;bottom:20px;right:20px;z-index:999999;background:#111827;color:#fef3c7;padding:16px 20px;border:2px solid #f59e0b;border-radius:12px;font-family:sans-serif;box-shadow:0 10px 25px rgba(0,0,0,0.5);min-width:300px;max-width:400px;\"; document.body.appendChild(box); let stopped = false; const stopBtn = document.createElement(\"button\"); stopBtn.innerText = \"\u23f9 Stop & afslut ordbog nu\"; stopBtn.style = \"margin-top:10px;padding:6px 12px;background:#b91c1c;color:#fff;border:none;border-radius:6px;font-weight:bold;cursor:pointer;font-size:12px;display:block;width:100%;\"; stopBtn.onclick = async () => { stopped = true; box.innerHTML = \"<b>\u23f9 Stopper...</b> Sender afslutning til serveren...\"; try { await fetch(host + \"/api/build/\" + bid + \"/skip-queue\", { method: \"POST\" }); } catch(e) {} box.innerHTML = \"<b>\u2705 Stoppet!</b> Ordbogen f\u00e6rdigg\u00f8res nu med de hentede ord.<br>Du kan lukke denne fane.\"; }; const url = host + \"/api/build/\" + bid + \"/missing-words\" + (l ? \"?limit=\" + l[1] : \"\"); const res = await fetch(url); const data = await res.json(); const items = data.items || (data.words || []).map(w => ({word: w, target: \"ods\", priority: 1})); if (items.length === 0) { box.innerHTML = \"<b>\u2705 Ingen ord mangler!</b> Alle ord er allerede fundet.\"; return; } const contentDiv = document.createElement(\"div\"); box.appendChild(contentDiv); box.appendChild(stopBtn); let baseDelay = 350; let jitterRange = 150; if (items.length > 500) { baseDelay = 1100; jitterRange = 700; } else if (items.length > 250) { baseDelay = 650; jitterRange = 450; } else if (items.length > 100) { baseDelay = 450; jitterRange = 250; } for (let i = 0; i < items.length; i++) { if (stopped) break; const it = items[i]; const w = typeof it === \"string\" ? it : it.word; const target = (it && it.target === \"ddo\") ? \"ddo\" : \"ods\"; const prioLabel = (it && it.priority === 2) ? \" [Prio 2: DDO]\" : \" [Prio 1: ODS]\"; contentDiv.innerHTML = \"<b>\u26a1 Ordpresse Lyn-Henter</b><br>Henter <b>\" + (i+1) + \" / \" + items.length + \"</b>\" + prioLabel + \": <code style='color:#f59e0b;font-size:14px;'>\" + w + \"</code>\"; try { const endpoint = target === \"ddo\" ? \"https://ordnet.dk/ddo/ordbog?query=\" : \"https://ordnet.dk/ods/ordbog?query=\"; const r = await fetch(endpoint + encodeURIComponent(w)); if (r.status === 429) { contentDiv.innerHTML = \"<b>\u26a0\ufe0f ordnet.dk bad om en pause (HTTP 429).</b><br>Venter 20 sekunder f\u00f8r automatisk genoptagelse...\"; baseDelay += 400; jitterRange += 300; await new Promise(res => setTimeout(res, 20000)); i--; continue; } if (r.ok) { const html = await r.text(); await fetch(host + \"/api/cache/submit\", { method: \"POST\", headers: {\"Content-Type\": \"application/json\"}, body: JSON.stringify({word: w, html: html, build_id: bid}) }); } else { console.warn(\"Uventet HTTP status for \" + w + \": \" + r.status); } } catch (e) { console.error(\"Fejl under hentning af \" + w + \":\", e); } const progressiveExtra = (i > 500) ? Math.min(500, Math.floor((i - 500) / 100) * 50) : 0; const actualDelay = baseDelay + progressiveExtra + Math.floor(Math.random() * jitterRange); await new Promise(r => setTimeout(r, actualDelay)); } if (!stopped) { try { await fetch(host + \"/api/build/\" + bid + \"/skip-queue\", { method: \"POST\" }); } catch(e) {} box.innerHTML = \"<b>\u2705 F\u00e6rdig!</b> Alle \" + items.length + \" ord er sendt til Ordpresse.<br>Serveren kompilerer nu din ordbog f\u00e6rdig.\"; } })();";
    }

    // Build form submission with SSE
    if (buildForm) {
        buildForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            if (currentEvtSource) {
                currentEvtSource.close();
                currentEvtSource = null;
            }

            // Reset funnel stats
            const bVal = document.getElementById('stat-val-books');
            const bSub = document.getElementById('stat-sub-books');
            const lVal = document.getElementById('stat-val-local');
            const lSub = document.getElementById('stat-sub-local');
            const aVal = document.getElementById('stat-val-archaic');
            const aSub = document.getElementById('stat-sub-archaic');
            const pVal = document.getElementById('stat-val-package');
            const pSub = document.getElementById('stat-sub-package');

            if (bVal) bVal.textContent = 'Afventer...';
            if (bSub) bSub.textContent = 'Unikke ordformer';
            if (lVal) lVal.textContent = 'Afventer...';
            if (lSub) lSub.textContent = 'COR & ODS cache';
            if (aVal) aVal.textContent = 'Afventer...';
            if (aSub) aSub.textContent = 'Til ODS berigelse';
            if (pVal) pVal.textContent = 'Afventer...';
            if (pSub) pSub.textContent = '.idx & .syn';

            lastLogText = null;
            lastLogElement = null;

            // Prepare UI
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.classList.add('opacity-50', 'cursor-not-allowed');
            }
            if (progressCard) progressCard.classList.remove('hidden');
            if (completionBanner) completionBanner.classList.add('hidden');
            if (cancelledBanner) cancelledBanner.classList.add('hidden');
            if (cancelBuildBtn) {
                cancelBuildBtn.classList.remove('hidden');
                cancelBuildBtn.disabled = false;
                cancelBuildBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            }
            if (statusSpinner) statusSpinner.classList.remove('hidden');
            if (progressStatusTitle) progressStatusTitle.textContent = 'Bygger ordbog...';
            if (progressBarFill) progressBarFill.style.width = '5%';
            if (progressPercent) progressPercent.textContent = '5%';
            if (progressMessage) progressMessage.textContent = 'Sender forespørgsel til server...';
            if (logBox) logBox.innerHTML = '<div class="text-slate-400">[Start] Initialiserer opgave...</div>';

            const formData = new FormData(buildForm);

            try {
                const res = await fetch('/api/build', {
                    method: 'POST',
                    body: formData
                });
                if (!res.ok) {
                    throw new Error(`Fejl ved start af byggejob: ${res.statusText}`);
                }
                const data = await res.json();
                const buildId = data.build_id;
                currentBuildId = buildId;
                updateOpenOrdnetLinkHref();
                if (peerHelperPanel) {
                    peerHelperPanel.classList.remove('hidden');
                }
                if (skipQueueBtn) {
                    skipQueueBtn.disabled = false;
                    skipQueueBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                }
                if (startServerScrapeBtn) {
                    startServerScrapeBtn.disabled = false;
                    startServerScrapeBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                }
                updateMissingWordsCount(buildId);
                startMissingWordsPolling(buildId);

                // Connect to SSE stream
                const evtSource = new EventSource(`/api/build/events/${buildId}`);
                currentEvtSource = evtSource;

                evtSource.onmessage = (event) => {
                    try {
                        const evData = JSON.parse(event.data);
                        if (evData.percent !== undefined && progressBarFill && progressPercent) {
                            progressBarFill.style.width = `${evData.percent}%`;
                            progressPercent.textContent = `${evData.percent}%`;
                        }
                        if (evData.telemetry) {
                            updateTelemetry(evData.telemetry);
                        }
                        if (evData.message) {
                            if (progressMessage) progressMessage.textContent = evData.message;
                            if (peerHelperPanel && currentBuildId) {
                                peerHelperPanel.classList.remove('hidden');
                            }
                            if (evData.message.startsWith('⚡ Lyn-Henter aktiv:') || evData.message.startsWith('🚀 Lyn-Henter aktiv:')) {
                                const m = evData.message.match(/(\d+) ord modtaget/);
                                const missingWordsBadge = document.getElementById('missing-words-badge');
                                if (m && missingWordsBadge) {
                                    missingWordsBadge.textContent = `${m[1]} ord hentet`;
                                }
                            }
                            appendLog(evData.message);
                        }
                        if (evData.status === 'done') {
                            stopMissingWordsPolling();
                            if (statusSpinner) statusSpinner.classList.add('hidden');
                            if (cancelBuildBtn) cancelBuildBtn.classList.add('hidden');
                            if (progressStatusTitle) progressStatusTitle.textContent = 'Færdigbygget!';
                            const entries = evData.entry_count !== undefined ? evData.entry_count : 0;
                            const syns = evData.synonym_count !== undefined ? evData.synonym_count : '';
                            const fmt = (n) => (typeof n === 'number') ? n.toLocaleString('da-DK') : n;
                            if (completionStats) {
                                completionStats.textContent = syns ? `${fmt(entries)} hovedord og ${fmt(syns)} synonymer indekseret.` : `${fmt(entries)} hovedord indekseret.`;
                            }

                            if (pVal && entries) pVal.textContent = `${fmt(entries)} opslag`;
                            if (pSub && syns) pSub.textContent = `${fmt(syns)} bøjninger`;

                            if (downloadBtn) downloadBtn.href = evData.download_url || `/api/download/${buildId}`;
                            if (peerHelperPanel) peerHelperPanel.classList.add('hidden');
                            if (completionBanner) completionBanner.classList.remove('hidden');
                            evtSource.close();
                            if (submitBtn) {
                                submitBtn.disabled = false;
                                submitBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                            }
                        } else if (evData.status === 'cancelled') {
                            stopMissingWordsPolling();
                            if (statusSpinner) statusSpinner.classList.add('hidden');
                            if (cancelBuildBtn) cancelBuildBtn.classList.add('hidden');
                            if (progressStatusTitle) progressStatusTitle.textContent = 'Bygning afbrudt';
                            if (progressMessage) progressMessage.textContent = 'Byggeprocessen blev afbrudt af brugeren.';
                            if (peerHelperPanel) peerHelperPanel.classList.add('hidden');
                            if (cancelledBanner) cancelledBanner.classList.remove('hidden');
                            evtSource.close();
                            if (submitBtn) {
                                submitBtn.disabled = false;
                                submitBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                            }
                        } else if (evData.status === 'error') {
                            stopMissingWordsPolling();
                            if (statusSpinner) statusSpinner.classList.add('hidden');
                            if (cancelBuildBtn) cancelBuildBtn.classList.add('hidden');
                            if (progressStatusTitle) progressStatusTitle.textContent = 'Fejl under bygning';
                            if (progressMessage) progressMessage.textContent = `Fejl: ${evData.error}`;
                            if (peerHelperPanel) peerHelperPanel.classList.add('hidden');
                            evtSource.close();
                            if (submitBtn) {
                                submitBtn.disabled = false;
                                submitBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                            }
                        }
                    } catch (parseErr) {
                        console.error("Fejl ved parsing af SSE:", parseErr);
                    }
                };

                evtSource.onerror = (err) => {
                    console.warn("SSE forbindelse afbrudt:", err);
                };

            } catch (err) {
                stopMissingWordsPolling();
                if (progressMessage) progressMessage.textContent = `Kunne ikke starte byggejob: ${err.message}`;
                if (statusSpinner) statusSpinner.classList.add('hidden');
                if (cancelBuildBtn) cancelBuildBtn.classList.add('hidden');
                if (peerHelperPanel) peerHelperPanel.classList.add('hidden');
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                }
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', initBuild);
