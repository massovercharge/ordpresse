/**
 * Tab Navigation Controller for Den Danske Ordpresse
 */

const VALID_TABS = [
    'tab-dictionary',
    'tab-build',
    'tab-devices',
    'tab-store',
    'tab-admin'
];

const LEGACY_HASH_MAP = {
    '#plugin-store-section': 'tab-store',
    '#books-archive-section': 'tab-store',
    '#build-form': 'tab-build',
    '#koreader-plugin-section': 'tab-devices'
};

function switchTab(tabId, updateHash = true) {
    if (!VALID_TABS.includes(tabId)) {
        tabId = 'tab-dictionary';
    }

    // Update panels
    const panels = document.querySelectorAll('.tab-panel');
    panels.forEach(panel => {
        if (panel.id === tabId) {
            panel.classList.remove('hidden');
        } else {
            panel.classList.add('hidden');
        }
    });

    // Update desktop tab buttons
    const buttons = document.querySelectorAll('.tab-btn');
    buttons.forEach(btn => {
        const target = btn.getAttribute('data-target');
        if (target === tabId) {
            btn.classList.add('active', 'text-amber-400', 'bg-slate-800/90', 'border-amber-400/80');
            btn.classList.remove('text-slate-400', 'border-transparent');
            btn.setAttribute('aria-selected', 'true');
        } else {
            btn.classList.remove('active', 'text-amber-400', 'bg-slate-800/90', 'border-amber-400/80');
            btn.classList.add('text-slate-400', 'border-transparent');
            btn.setAttribute('aria-selected', 'false');
        }
    });

    // Update mobile select if present
    const mobileSelect = document.getElementById('mobile-tab-select');
    if (mobileSelect && mobileSelect.value !== tabId) {
        mobileSelect.value = tabId;
    }

    // Sync URL hash
    if (updateHash) {
        if (history.replaceState) {
            history.replaceState(null, null, '#' + tabId);
        } else {
            window.location.hash = '#' + tabId;
        }
    }

    // Contextual triggers when entering a tab
    if (tabId === 'tab-devices' && typeof loadApiKeys === 'function') {
        loadApiKeys();
    } else if (tabId === 'tab-store') {
        if (typeof loadStoreMirrors === 'function') loadStoreMirrors();
        if (typeof loadBooksArchive === 'function') loadBooksArchive();
    } else if (tabId === 'tab-admin' && typeof loadAdminStats === 'function') {
        loadAdminStats();
    }
}

function initTabs() {
    // Bind click events on tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const target = btn.getAttribute('data-target');
            if (target) switchTab(target);
        });
    });

    // Bind change event on mobile select
    const mobileSelect = document.getElementById('mobile-tab-select');
    if (mobileSelect) {
        mobileSelect.addEventListener('change', (e) => {
            switchTab(e.target.value);
        });
    }

    // Bind any [data-tab-switch] buttons or links
    document.addEventListener('click', (e) => {
        const switchTrigger = e.target.closest('[data-tab-switch]');
        if (switchTrigger) {
            e.preventDefault();
            const target = switchTrigger.getAttribute('data-tab-switch');
            if (target) {
                switchTab(target);
                const scrollTarget = switchTrigger.getAttribute('data-scroll-to');
                if (scrollTarget) {
                    const el = document.getElementById(scrollTarget);
                    if (el) el.scrollIntoView({ behavior: 'smooth' });
                }
            }
        }
    });

    // Determine initial tab from hash
    let initialHash = window.location.hash;
    if (LEGACY_HASH_MAP[initialHash]) {
        switchTab(LEGACY_HASH_MAP[initialHash], true);
    } else if (initialHash && initialHash.startsWith('#')) {
        const cleanId = initialHash.substring(1);
        if (VALID_TABS.includes(cleanId)) {
            switchTab(cleanId, false);
        } else {
            switchTab('tab-dictionary', false);
        }
    } else {
        switchTab('tab-dictionary', false);
    }

    // Handle browser back/forward buttons
    window.addEventListener('hashchange', () => {
        const hash = window.location.hash;
        if (LEGACY_HASH_MAP[hash]) {
            switchTab(LEGACY_HASH_MAP[hash], false);
        } else if (hash && hash.startsWith('#')) {
            const cleanId = hash.substring(1);
            if (VALID_TABS.includes(cleanId)) {
                switchTab(cleanId, false);
            }
        }
    });
}

document.addEventListener('DOMContentLoaded', initTabs);

