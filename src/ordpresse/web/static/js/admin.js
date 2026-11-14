/**
 * Admin Panel & Password Modal Controller
 */

window.togglePasswordModal = function (show) {
    const modal = document.getElementById('change-pwd-modal');
    const feedback = document.getElementById('change-pwd-feedback');
    const form = document.getElementById('change-pwd-form');
    const curInput = document.getElementById('current-pwd-input');
    if (!modal) return;
    if (show) {
        modal.classList.remove('hidden');
        if (feedback) feedback.className = 'hidden';
        if (form) form.reset();
        if (curInput) setTimeout(() => curInput.focus(), 50);
    } else {
        modal.classList.add('hidden');
    }
};

async function loadAdminStats() {
    const statsContainer = document.getElementById('admin-stats-container');
    if (!statsContainer) return;

    try {
        const res = await fetch('/api/stats');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        const datasetsStatusEl = document.getElementById('stat-datasets-size');
        const cacheStatusEl = document.getElementById('stat-cache-size');

        if (datasetsStatusEl) {
            datasetsStatusEl.textContent = data.datasets_sqlite_exists 
                ? `${data.datasets_sqlite_size_mb} MB (Klar)` 
                : 'Mangler / Offline';
        }
        if (cacheStatusEl) {
            cacheStatusEl.textContent = data.cache_sqlite_exists 
                ? `${data.cache_sqlite_size_mb} MB (Aktiv)` 
                : 'Mangler';
        }
    } catch (err) {
        console.error("Kunne ikke hente admin stats:", err);
    }
}

function initAdmin() {
    const changePwdModal = document.getElementById('change-pwd-modal');
    const changePwdForm = document.getElementById('change-pwd-form');

    if (changePwdModal) {
        changePwdModal.addEventListener('click', (e) => {
            if (e.target === changePwdModal) window.togglePasswordModal(false);
        });
    }

    if (changePwdForm) {
        changePwdForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const curPwd = document.getElementById('current-pwd-input').value;
            const newPwd = document.getElementById('new-pwd-input').value;
            const confPwd = document.getElementById('confirm-pwd-input').value;
            const feedback = document.getElementById('change-pwd-feedback');
            const submitBtn = document.getElementById('save-change-pwd-btn');

            if (newPwd !== confPwd) {
                if (feedback) {
                    feedback.className = 'text-xs p-2.5 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 block';
                    feedback.textContent = 'De to nye adgangskoder stemmer ikke overens.';
                }
                return;
            }

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.classList.add('opacity-50');
            }

            try {
                const res = await fetch('/api/v1/auth/change-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        current_password: curPwd,
                        new_password: newPwd,
                        confirm_password: confPwd,
                    })
                });

                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || 'Kunne ikke skifte adgangskode.');
                }

                if (feedback) {
                    feedback.className = 'text-xs p-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 block';
                    feedback.textContent = '✅ Adgangskoden er nu opdateret!';
                }
                setTimeout(() => {
                    window.togglePasswordModal(false);
                }, 1500);
            } catch (err) {
                if (feedback) {
                    feedback.className = 'text-xs p-2.5 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 block';
                    feedback.textContent = err.message;
                }
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.classList.remove('opacity-50');
                }
            }
        });
    }

    // Refresh stats if in view
    loadAdminStats();
}

document.addEventListener('DOMContentLoaded', initAdmin);
