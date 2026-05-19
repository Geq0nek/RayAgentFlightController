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
    let filterOptions = {};
    let persisted = { logs: [] };
    let dbStatus = {};
    try {
        [reports, filterOptions, persisted, dbStatus] = await Promise.all([
            ATC_API.getTickReports(20),
            ATC_API.getLogFilterOptions(),
            ATC_API.getPersistedLogs({ limit: 100 }),
            ATC_API.getLogDatabaseStatus(),
        ]);
    } catch (e) {
        container.innerHTML = `<p style="color:#c0392b">API connection error: ${e.message}</p>`;
        return;
    }

    container.innerHTML = _renderHistoryPage({
        reports: reports || [],
        filterOptions: filterOptions || {},
        logs: persisted.logs || [],
        dbStatus: dbStatus || {},
    });
    _bindHistoryFilters();
}

function _renderHistoryPage({ reports, filterOptions, logs, dbStatus }) {
    const dbOk = dbStatus && dbStatus.last_error ? false : true;
    const sourceOptions = filterOptions.sources || [];
    const targetOptions = filterOptions.targets || [];
    const eventOptions = filterOptions.event_types || [];
    const flightOptions = filterOptions.flight_ids || [];

    const logRows = logs.length ? logs.map(log => `
        <tr>
            <td>${log.id}</td>
            <td>${log.tick ?? '—'}</td>
            <td>${_escapeHtml(log.event_type || '')}</td>
            <td>${_escapeHtml(log.source_voivodeship || '—')}</td>
            <td>${_escapeHtml(log.target_voivodeship || '—')}</td>
            <td>${_escapeHtml(log.flight_id || '—')}</td>
            <td>${_escapeHtml(log.message || '')}</td>
            <td>${_formatLogTime(log.created_at)}</td>
        </tr>
    `).join('') : `
        <tr><td colspan="8" class="empty-cell">Brak zapisanych logów dla wybranych filtrów.</td></tr>
    `;

    const reportRows = reports.length ? reports.slice().reverse().map(r => {
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
    }).join('') : `
        <tr><td colspan="6" class="empty-cell">Brak raportów ticków. Uruchom symulację.</td></tr>
    `;

    return `
        <div class="history-toolbar">
            <div class="filter-grid">
                ${_renderMultiSelect('history-source', 'Źródło', sourceOptions, 'źródła')}
                ${_renderMultiSelect('history-target', 'Cel', targetOptions, 'cele')}
                ${_renderMultiSelect('history-event-type', 'Typ', eventOptions, 'typy')}
                ${_renderMultiSelect('history-flight-id', 'ID lotu', flightOptions, 'loty')}
                <label>Od ticka
                    <input id="history-tick-from" type="number" min="0">
                </label>
                <label>Do ticka
                    <input id="history-tick-to" type="number" min="0">
                </label>
                <label>Tekst
                    <input id="history-query" type="text" placeholder="handoff, snapshot...">
                </label>
                <label>Limit
                    <input id="history-limit" type="number" min="1" max="500" value="100">
                </label>
            </div>
            <button id="history-apply-filters">Filtruj</button>
            <span class="${dbOk ? 'db-status-ok' : 'db-status-warn'}">
                DB: ${dbOk ? 'aktywny' : _escapeHtml(dbStatus.last_error || 'błąd')}
            </span>
        </div>

        <h3 class="history-section-title">Logi agentów z PostgreSQL</h3>
        <div class="table-scroll">
            <table class="history-table history-log-table">
                <thead>
                    <tr>
                        <th>ID</th><th>Tick</th><th>Typ</th><th>Źródło</th>
                        <th>Cel</th><th>Lot</th><th>Wiadomość</th><th>Czas</th>
                    </tr>
                </thead>
                <tbody id="history-log-body">${logRows}</tbody>
            </table>
        </div>

        <h3 class="history-section-title">Raporty ticków</h3>
        <table class="history-table">
            <thead>
                <tr>
                    <th>Tick</th><th>Czas sym.</th><th>Aktywne</th>
                    <th>Handoff-y</th><th>Przyloty</th><th>Ostrzeżenia</th>
                </tr>
            </thead>
            <tbody>${reportRows}</tbody>
        </table>`;
}

function _renderMultiSelect(id, title, options, pluralLabel) {
    const rows = options.map((value, index) => {
        const inputId = `${id}-${index}`;
        return `<label class="multi-option" for="${inputId}">
            <input id="${inputId}" type="checkbox" value="${_escapeHtml(value)}">
            <span>${_escapeHtml(value)}</span>
        </label>`;
    }).join('');
    return `
        <div class="filter-field">
            <span>${title}</span>
            <div class="multi-select" id="${id}" data-many-label="${pluralLabel}">
                <button class="multi-select-toggle" type="button" aria-expanded="false">
                    <span class="multi-select-label">Wszystkie</span>
                    <span class="multi-select-arrow">v</span>
                </button>
                <div class="multi-select-menu" hidden>
                    ${rows || '<div class="multi-empty">Brak opcji</div>'}
                </div>
            </div>
        </div>`;
}

function _bindHistoryFilters() {
    const btn = document.getElementById('history-apply-filters');
    if (btn) btn.addEventListener('click', applyHistoryFilters);
    document.querySelectorAll('.multi-select').forEach(_bindHistoryMultiDropdown);
    const query = document.getElementById('history-query');
    if (query) {
        query.addEventListener('keydown', event => {
            if (event.key === 'Enter') applyHistoryFilters();
        });
    }
}

function _bindHistoryMultiDropdown(dropdown) {
    if (!dropdown) return;
    const toggle = dropdown.querySelector('.multi-select-toggle');
    const menu = dropdown.querySelector('.multi-select-menu');
    const label = dropdown.querySelector('.multi-select-label');
    const checkboxes = Array.from(dropdown.querySelectorAll('input[type="checkbox"]'));
    const manyLabel = dropdown.dataset.manyLabel || 'opcje';

    const updateLabel = () => {
        const selected = checkboxes.filter(input => input.checked).map(input => input.value);
        if (selected.length === 0) {
            label.textContent = 'Wszystkie';
        } else if (selected.length === 1) {
            label.textContent = selected[0];
        } else {
            label.textContent = `${selected.length} ${manyLabel}`;
        }
    };

    toggle.addEventListener('click', event => {
        event.stopPropagation();
        const isOpen = !menu.hidden;
        menu.hidden = isOpen;
        toggle.setAttribute('aria-expanded', String(!isOpen));
    });

    menu.addEventListener('click', event => event.stopPropagation());
    checkboxes.forEach(input => input.addEventListener('change', updateLabel));

    document.addEventListener('click', event => {
        if (!dropdown.contains(event.target)) {
            menu.hidden = true;
            toggle.setAttribute('aria-expanded', 'false');
        }
    });
}

async function applyHistoryFilters() {
    const body = document.getElementById('history-log-body');
    if (!body) return;
    body.innerHTML = '<tr><td colspan="8" class="empty-cell">Ładowanie...</td></tr>';
    try {
        const data = await ATC_API.getPersistedLogs(_historyFilterValues());
        const rows = (data.logs || []).map(log => `
            <tr>
                <td>${log.id}</td>
                <td>${log.tick ?? '—'}</td>
                <td>${_escapeHtml(log.event_type || '')}</td>
                <td>${_escapeHtml(log.source_voivodeship || '—')}</td>
                <td>${_escapeHtml(log.target_voivodeship || '—')}</td>
                <td>${_escapeHtml(log.flight_id || '—')}</td>
                <td>${_escapeHtml(log.message || '')}</td>
                <td>${_formatLogTime(log.created_at)}</td>
            </tr>
        `).join('');
        body.innerHTML = rows || '<tr><td colspan="8" class="empty-cell">Brak zapisanych logów dla wybranych filtrów.</td></tr>';
    } catch (e) {
        body.innerHTML = `<tr><td colspan="8" class="empty-cell error-cell">API connection error: ${_escapeHtml(e.message)}</td></tr>`;
    }
}

function _historyFilterValues() {
    return {
        source: _selectedMultiValues('history-source'),
        target: _selectedMultiValues('history-target'),
        event_types: Array.from(document.querySelectorAll('#history-event-type input[type="checkbox"]:checked'))
            .map(input => input.value),
        flight_id: _selectedMultiValues('history-flight-id'),
        tick_from: document.getElementById('history-tick-from')?.value || '',
        tick_to: document.getElementById('history-tick-to')?.value || '',
        q: document.getElementById('history-query')?.value.trim() || '',
        limit: document.getElementById('history-limit')?.value || 100,
    };
}

function _selectedMultiValues(id) {
    return Array.from(document.querySelectorAll(`#${id} input[type="checkbox"]:checked`))
        .map(input => input.value);
}

function _formatLogTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return _escapeHtml(value);
    return date.toLocaleString('pl-PL');
}

function _escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

// ============================================================
// Start
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    startRadarPolling(2000);
});
