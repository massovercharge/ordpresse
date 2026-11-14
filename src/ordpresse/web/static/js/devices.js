/**
 * KOReader Devices & API Keys Management Controller
 */

async function loadApiKeys() {
    const apiKeysTbody = document.getElementById('api-keys-tbody');
    if (!apiKeysTbody) return;

    try {
        const res = await fetch('/api/v1/keys');
        if (res.status === 401) {
            apiKeysTbody.innerHTML = '<tr><td colspan="6" class="py-4 text-center text-amber-400">Log ind påkrævet for at se enhedsnøgler.</td></tr>';
            return;
        }
        const keys = await res.json();
        if (!Array.isArray(keys) || keys.length === 0) {
            apiKeysTbody.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-slate-500 italic">Ingen enhedsnøgler oprettet endnu. Generer en ovenfor!</td></tr>';
            return;
        }

        apiKeysTbody.innerHTML = keys.map(k => {
            const isActive = k.is_active === 1;
            const statusBadge = isActive
                ? '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">● Aktiv</span>'
                : '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-500/10 text-red-400 border border-red-500/20">○ Tilbagekaldt</span>';

            let lastUsedStr = "Aldrig brugt";
            if (k.last_used_at) {
                try {
                    const d = new Date(k.last_used_at);
                    lastUsedStr = d.toLocaleDateString('da-DK', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
                } catch (e) {
                    lastUsedStr = k.last_used_at;
                }
            }

            const actionBtn = isActive
                ? `<button type="button" onclick="revokeApiKey(${k.id})" class="px-2.5 py-1 bg-red-500/10 hover:bg-red-500/20 text-red-300 border border-red-500/30 rounded-lg text-[11px] font-medium transition-all active:scale-95 cursor-pointer">Tilbagekald</button>`
                : `<button type="button" onclick="deleteApiKey(${k.id})" class="px-2.5 py-1 bg-slate-800 hover:bg-red-500/20 text-slate-400 hover:text-red-300 border border-slate-700 hover:border-red-500/30 rounded-lg text-[11px] font-medium transition-all active:scale-95 cursor-pointer">Slet</button>`;

            const lookups = (typeof k.lookup_count === 'number') ? k.lookup_count.toLocaleString('da-DK') : (k.lookup_count || 0);

            return `
                <tr class="hover:bg-slate-900/40 transition-colors">
                    <td class="py-2.5 px-4 font-semibold text-slate-200">${escapeHtml(k.device_name)}</td>
                    <td class="py-2.5 px-4"><span class="font-mono text-[11px] bg-slate-900 text-amber-300 px-2 py-0.5 rounded border border-slate-800">${escapeHtml(k.key_prefix)}...</span></td>
                    <td class="py-2.5 px-4">${statusBadge}</td>
                    <td class="py-2.5 px-4 text-slate-300 font-mono">${lookups}</td>
                    <td class="py-2.5 px-4 text-slate-400 text-[11px]">${lastUsedStr}</td>
                    <td class="py-2.5 px-4 text-right">${actionBtn}</td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        console.error("Fejl ved hentning af API-nøgler:", err);
        apiKeysTbody.innerHTML = `<tr><td colspan="6" class="py-4 text-center text-red-400">Kunne ikke hente enhedsnøgler: ${escapeHtml(err.message)}</td></tr>`;
    }
}

window.revokeApiKey = async function (keyId) {
    if (!confirm("Er du sikker på, at du vil tilbagekalde denne API-nøgle? Enheden vil ikke længere have adgang til online opslag.")) return;
    try {
        const res = await fetch(`/api/v1/keys/${keyId}/revoke`, { method: 'POST' });
        if (res.ok) {
            loadApiKeys();
        } else {
            alert("Kunne ikke tilbagekalde nøgle.");
        }
    } catch (e) {
        alert("Fejl ved tilbagekaldelse: " + e.message);
    }
};

window.deleteApiKey = async function (keyId) {
    if (!confirm("Vil du slette denne nøgle permanent fra databasen?")) return;
    try {
        const res = await fetch(`/api/v1/keys/${keyId}`, { method: 'DELETE' });
        if (res.ok) {
            loadApiKeys();
        } else {
            alert("Kunne ikke slette nøgle.");
        }
    } catch (e) {
        alert("Fejl ved sletning: " + e.message);
    }
};

function initDevices() {
    const provisionForm = document.getElementById('provision-form');
    const deviceNameInput = document.getElementById('device-name-input');
    const endpointUrlInput = document.getElementById('endpoint-url-input');
    const provisionSubmitBtn = document.getElementById('provision-submit-btn');
    const provisionFeedback = document.getElementById('provision-feedback');
    const refreshKeysBtn = document.getElementById('refresh-keys-btn');

    if (refreshKeysBtn) {
        refreshKeysBtn.addEventListener('click', () => loadApiKeys());
    }

    if (provisionForm) {
        provisionForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const deviceName = deviceNameInput.value.trim();
            const endpointUrl = endpointUrlInput.value.trim() || 'https://ordpresse.seame.click/api/v1/koreader/lookup';

            if (!deviceName) return;

            provisionSubmitBtn.disabled = true;
            provisionSubmitBtn.classList.add('opacity-50', 'cursor-not-allowed');
            provisionFeedback.className = 'text-xs p-3 rounded-lg bg-blue-500/10 border border-blue-500/30 text-blue-300 block';
            provisionFeedback.textContent = '⚡ Genererer API-nøgle og pakker ordpresse.koplugin.zip...';

            try {
                const customKeyEl = document.getElementById('custom-key-input');
                const customKeyVal = customKeyEl ? customKeyEl.value.trim() : null;

                const res = await fetch('/api/v1/keys/provision', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        device_name: deviceName,
                        endpoint_url: endpointUrl,
                        custom_key: customKeyVal || undefined
                    })
                });

                if (!res.ok) {
                    const err = await res.json().catch(() => ({ detail: 'Ukendt serverfejl' }));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }

                const blob = await res.blob();
                const contentDisp = res.headers.get('Content-Disposition') || '';
                let filename = `ordpresse-koplugin-${deviceName.toLowerCase().replace(/[^a-z0-9_-]+/g, '-')}.zip`;
                const match = contentDisp.match(/filename="?([^";]+)"?/);
                if (match && match[1]) filename = match[1];

                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);

                const pluginVer = res.headers.get('X-Plugin-Version') || window.ordpressePluginVersion || '1.2.6';
                const rawKeyHeader = res.headers.get('X-Raw-Api-Key');
                if (rawKeyHeader) {
                    try {
                        localStorage.setItem('ordpresse_api_key', rawKeyHeader);
                        const opdsApiKeyInput = document.getElementById('opds-api-key-input');
                        if (opdsApiKeyInput) {
                            opdsApiKeyInput.value = rawKeyHeader;
                            if (typeof updateOpdsUrl === 'function') updateOpdsUrl();
                        }
                    } catch (e) { }
                }
                provisionFeedback.className = 'text-xs p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 block';
                provisionFeedback.textContent = `✅ API-nøgle oprettet for "${deviceName}", og "${filename}" (Plugin v${pluginVer}) er hentet!`;
                deviceNameInput.value = '';

                loadApiKeys();
            } catch (err) {
                provisionFeedback.className = 'text-xs p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 block';
                provisionFeedback.textContent = `Fejl under generering: ${err.message}`;
            } finally {
                provisionSubmitBtn.disabled = false;
                provisionSubmitBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            }
        });
    }

    // Initial load
    loadApiKeys();
}

document.addEventListener('DOMContentLoaded', initDevices);

