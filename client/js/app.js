// ============================================================
// Nawigacja
// ============================================================
function showPage(pageId) {
    document.querySelectorAll('.page-view').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));

    const selectedPage = document.getElementById('page-' + pageId);
    const selectedBtn  = document.getElementById('btn-' + pageId);

    if (selectedPage && selectedBtn) {
        selectedPage.classList.add('active');
        selectedBtn.classList.add('active');
    }

    if (pageId === 'radar') {
        setTimeout(() => mapRadar.map.invalidateSize(), 100);
    }

    if (pageId === 'history') {
        loadHistoryPage();
    }
}

// ============================================================
// Live radar — polling co 2 s
// ============================================================
let _radarInterval = null;

async function refreshRadar() {
    try {
        const [flights, voivData, status] = await Promise.all([
            ATC_API.getAllFlights(),
            ATC_API.getVoivodeships(),
            ATC_API.getStatus(),
        ]);
        mapRadar.updateRadar(flights, voivData);
        _updateStatusBar(status, flights.length);
    } catch (e) {
        console.warn("[radar] API niedostępne:", e.message);
    }
}

function _updateStatusBar(status, flightCount) {
    const bar = document.getElementById('status-bar');
    if (!bar) return;
    const isRunning = status.is_running !== false;
    bar.innerHTML =
        `<span class="${isRunning ? 'dot-green' : 'dot-red'}"></span>` +
        `Tick: <b>${status.tick ?? '—'}</b> &nbsp;|&nbsp;` +
        `Czas sym.: <b>${status.sim_time ?? '—'}</b> &nbsp;|&nbsp;` +
        `Aktywne loty: <b>${flightCount}</b>`;
}

function startRadarPolling(intervalMs = 2000) {
    if (_radarInterval) return;
    refreshRadar();
    _radarInterval = setInterval(refreshRadar, intervalMs);
}

function stopRadarPolling() {
    if (_radarInterval) {
        clearInterval(_radarInterval);
        _radarInterval = null;
    }
}

// ============================================================
// History page — tactical reports and agent logs
// ============================================================
async function loadHistoryPage() {
    const container = document.getElementById('history-container');
    if (!container) return;
    container.innerHTML = '<p style="color:#888">Loading...</p>';

    let reports = [];
    try {
        reports = await ATC_API.getTickReports(20);
    } catch (e) {
        container.innerHTML = `<p style="color:#c0392b">API connection error: ${e.message}</p>`;
        return;
    }

    if (!reports || reports.length === 0) {
        container.innerHTML = '<p style="color:#888">No reports. Start simulation.</p>';
        return;
    }

    const rows = reports.slice().reverse().map(r => {
        const warnings = r.total_warnings > 0
            ? `<span class="badge badge-warn">${r.total_warnings} ost.</span>` : '';
        return `
            <tr>
                <td>${r.tick}</td>
                <td>${r.sim_time}</td>
                <td><b>${r.total_active}</b></td>
                <td>${r.total_handed_off}</td>
                <td>${r.total_arrived}</td>
                <td>${warnings || r.total_warnings}</td>
            </tr>`;
    }).join('');

    container.innerHTML = `
        <table class="history-table">
            <thead>
                <tr>
                    <th>Tick</th><th>Czas sym.</th><th>Aktywne</th>
                    <th>Handoff-y</th><th>Przyloty</th><th>Ostrzeżenia</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>`;
}

// ============================================================
// Start
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    startRadarPolling(2000);
});

