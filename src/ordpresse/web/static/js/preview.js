/**
 * E-Ink Preview logic for KOReader Dictionary Lookup
 */

async function doPreviewLookup(word) {
    if (!word) return;
    const previewInput = document.getElementById('preview-input');
    const einkDisplay = document.getElementById('eink-display');
    const previewSourceBadge = document.getElementById('preview-source-badge');

    if (previewInput) previewInput.value = word;
    if (einkDisplay) einkDisplay.style.opacity = '0.5';

    try {
        const res = await fetch(`/api/preview?q=${encodeURIComponent(word)}`);
        const data = await res.json();
        if (einkDisplay) {
            einkDisplay.innerHTML = data.html || "<div class='text-neutral-500 italic'>Ingen artikel fundet.</div>";
        }
        if (previewSourceBadge) {
            previewSourceBadge.textContent = data.resolution || (data.source ? `Kilde: ${data.source}` : 'Kilde: Kanonisk E-Ink v2');
        }
    } catch (err) {
        if (einkDisplay) {
            einkDisplay.innerHTML = `<div class="text-red-600">Fejl ved opslag: ${escapeHtml(err.message)}</div>`;
        }
    } finally {
        if (einkDisplay) einkDisplay.style.opacity = '1';
    }
}

function initPreview() {
    const previewForm = document.getElementById('preview-form');
    const previewInput = document.getElementById('preview-input');
    const previewChips = document.querySelectorAll('.preview-chip');

    if (previewForm && previewInput) {
        previewForm.addEventListener('submit', (e) => {
            e.preventDefault();
            doPreviewLookup(previewInput.value.trim());
        });
    }

    previewChips.forEach(chip => {
        chip.addEventListener('click', () => {
            const w = chip.getAttribute('data-word');
            doPreviewLookup(w);
        });
    });

    // Automatically load initial preview on page load
    doPreviewLookup('lægdslemmer');
}

document.addEventListener('DOMContentLoaded', initPreview);

