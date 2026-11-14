/**
 * Shared utility functions for Ordpresse Web UI
 */

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

function formatNumber(n) {
    if (typeof n === 'number') {
        return n.toLocaleString('da-DK');
    }
    return n;
}

function formatBytes(bytes) {
    if (!bytes || isNaN(bytes)) return '-';
    if (bytes >= 1024 * 1024 * 1024) {
        return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
    }
    if (bytes >= 1024 * 1024) {
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }
    if (bytes >= 1024) {
        return Math.round(bytes / 1024) + ' KB';
    }
    return bytes + ' B';
}

function formatDate(dateInput) {
    if (!dateInput) return '-';
    try {
        const d = new Date(dateInput);
        if (isNaN(d.getTime())) return dateInput;
        return d.toLocaleDateString('da-DK', {
            day: '2-digit',
            month: 'short',
            year: 'numeric'
        });
    } catch (e) {
        return dateInput;
    }
}
