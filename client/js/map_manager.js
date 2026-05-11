class MapManager {
    constructor(mapContainerId, options = {}) {
        this.options = {
            initialView: options.initialView || [52.2297, 21.0122],
            initialZoom: options.initialZoom || 6,
            maxZoom: options.maxZoom || 19,
            geojsonPath: './pol_admin_boundaries/pol_admin1_em.geojson',
            airportDataPath: '/configuration/airports_informations.yaml',
            towerIconUrl: './assets/control_tower.png',
            towerIconSize: options.towerIconSize || [50, 50],
            towerIconAnchor: [25, 50], 
            panelId: 'info-panel',
            contentId: 'panel-content'
        };

        this.map = L.map(mapContainerId).setView(this.options.initialView, this.options.initialZoom);

        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: this.options.maxZoom,
            attribution: '&copy; OpenStreetMap'
        }).addTo(this.map);

        this.towerIcon = L.icon({
            iconUrl: this.options.towerIconUrl,
            iconSize: this.options.towerIconSize,
            iconAnchor: this.options.towerIconAnchor,
            popupAnchor: [0, -50]
        });

        this.panel = document.getElementById(this.options.panelId);
        this.content = document.getElementById(this.options.contentId);

        // Stan live-radarowy
        this._flightMarkers = {};      // flight_id → L.Marker
        this._flightTrails  = {};      // flight_id → L.Polyline (ślad)
        this._flightHistory = {};      // flight_id → [[lat,lon], ...]
        this._voivColors    = {};      // voivodeship_key → kolor hex
        this._towerMarkers  = {};      // voivodeship_key → L.Marker (wieże)
        this._voivPolygons  = {};      // voivodeship_key → L.GeoJSON layer
        this._selectedVoiv  = null;    // aktualnie podświetlone województwo
        this._voivData      = {};      // voivodeship_key → dane z YAML (lotniska)

        // Paleta kolorów dla wież (16 województw)
        this._palette = [
            "#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6",
            "#1abc9c","#e67e22","#34495e","#e91e63","#00bcd4",
            "#8bc34a","#ff5722","#607d8b","#9c27b0","#ff9800","#795548"
        ];
    }

    async init() {
        await this.set_voivodeships_border();
        await this.load_airports();
    }

    async set_voivodeships_border() {
        try {
            const response = await fetch(this.options.geojsonPath);
            if (!response.ok) throw new Error("GeoJSON not found");
            const data = await response.json();

            // Mapowanie GeoJSON adm1_name → klucz topologii (taki sam jak w actor.py)
            const geoToKey = {
                "Dolnośląskie":        "dolnoslaskie",
                "Kujawsko-Pomorskie":  "kujawsko_pomorskie",
                "Lubelskie":           "lubelskie",
                "Lubuskie":            "lubuskie",
                "Łódzkie":             "lodzkie",
                "Małopolskie":         "malopolskie",
                "Mazowieckie":         "mazowieckie",
                "Opolskie":            "opolskie",
                "Podkarpackie":        "podkarpackie",
                "Podlaskie":           "podlaskie",
                "Pomorskie":           "pomorskie",
                "Śląskie":             "slaskie",
                "Świętokrzyskie":      "swietokrzyskie",
                "Warmińsko-Mazurskie": "warminsko_mazurskie",
                "Wielkopolskie":       "wielkopolskie",
                "Zachodniopomorskie":  "zachodniopomorskie",
            };

            // Przypisz kolor każdemu województwu
            const keys = Object.values(geoToKey);
            keys.forEach((k, i) => { this._voivColors[k] = this._palette[i % this._palette.length]; });

            data.features.forEach(feature => {
                const name = feature.properties.adm1_name;
                const key  = geoToKey[name] || name.toLowerCase();

                const layer = L.geoJSON(feature, {
                    style: {
                        color:       "#2c3e50",
                        weight:      2,
                        fillColor:   this._voivColors[key] || "#aaa",
                        fillOpacity: 0.08,
                    }
                }).addTo(this.map);

                this._voivPolygons[key] = layer;

                // Kliknięcie w województwo → pokaż panel agenta
                layer.on('click', () => this._showAgentPanel(key));
            });

            console.log("Borders loaded.");
        } catch (error) {
            console.error("Error loading borders:", error);
        }
    }

    async load_airports() {
        try {
            const response = await fetch(this.options.airportDataPath);
            if (!response.ok) throw new Error("YAML not found");
            const yamlText = await response.text();
            const data = jsyaml.load(yamlText);

            for (const [voivodeship, airports] of Object.entries(data.voivodeships)) {
                this._voivData[voivodeship] = airports;
                airports.forEach(airport => {
                    const color = this._voivColors[voivodeship] || "#555";

                    // Kolorowa wieża kontrolna (div icon zamiast obrazka)
                    const towerIcon = L.divIcon({
                        className: '',
                        html: `<div class="tower-marker" style="border-color:${color}">
                                 <img src="./assets/control_tower.png" style="width:36px;height:36px;">
                                 <div class="tower-dot" style="background:${color}"></div>
                               </div>`,
                        iconSize:   [40, 50],
                        iconAnchor: [20, 50],
                        popupAnchor:[0, -52],
                    });

                    const marker = L.marker(
                        [airport.location.latitude, airport.location.longitude],
                        { icon: towerIcon }
                    ).addTo(this.map);

                    marker.bindTooltip(
                        `<b>${airport.iata_code}</b><br>${voivodeship}`,
                        { direction: 'top', offset: [0, -50] }
                    );

                    marker.on('click', () => {
                        this._renderAirportPanel(airport, voivodeship);
                        this._highlightVoivodeship(voivodeship);
                    });

                    this._towerMarkers[voivodeship] = this._towerMarkers[voivodeship] || [];
                    this._towerMarkers[voivodeship].push(marker);
                });
            }
            console.log("Airports loaded.");
        } catch (error) {
            console.error("Error loading airports:", error);
        }
    }

    // ------------------------------------------------------------------
    // Live radar — wywoływane z app.js co N sekund
    // ------------------------------------------------------------------

    /**
     * Aktualizuje pozycje samolotów i podświetlenie województw.
     * @param {Array} flights  - tablica obiektów z /api/flights
     * @param {Array} voivData - tablica obiektów z /api/voivodeships
     */
    updateRadar(flights, voivData) {
        // 1. Zaktualizuj podświetlenie województw na podstawie liczby lotów
        this._updateVoivOpacity(voivData);

        // 2. Zbierz ID aktywnych lotów z tej odpowiedzi
        const activeIds = new Set(flights.map(f => f.id));

        // 3. Usuń markery lotów które już nie istnieją
        for (const id of Object.keys(this._flightMarkers)) {
            if (!activeIds.has(id)) {
                this.map.removeLayer(this._flightMarkers[id]);
                delete this._flightMarkers[id];
                if (this._flightTrails[id]) {
                    this.map.removeLayer(this._flightTrails[id]);
                    delete this._flightTrails[id];
                }
                delete this._flightHistory[id];
            }
        }

        // 4. Dodaj / aktualizuj markery
        flights.forEach(f => {
            const lat  = f.current_lat;
            const lon  = f.current_lon;
            const voiv = f.actual_voivodeship;
            const color = this._voivColors[voiv] || "#3498db";

            // Historia pozycji (ślad)
            if (!this._flightHistory[f.id]) this._flightHistory[f.id] = [];
            this._flightHistory[f.id].push([lat, lon]);
            if (this._flightHistory[f.id].length > 30) this._flightHistory[f.id].shift();

            // Ślad (polyline)
            if (this._flightTrails[f.id]) {
                this._flightTrails[f.id].setLatLngs(this._flightHistory[f.id]);
            } else {
                this._flightTrails[f.id] = L.polyline(
                    this._flightHistory[f.id],
                    { color, weight: 1.5, opacity: 0.5, dashArray: "4 4" }
                ).addTo(this.map);
            }

            // Marker samolotu
            if (this._flightMarkers[f.id]) {
                const marker = this._flightMarkers[f.id];
                marker.setLatLng([lat, lon]);
                marker._atcData = f;   // odśwież dane dla popupu
                // Zaktualizuj kolor jeśli zmieniło się województwo (handoff)
                marker.setIcon(this._planeIcon(color, f.id));
                if (this._flightTrails[f.id]) {
                    this._flightTrails[f.id].setStyle({ color });
                }
                // Odśwież popup jeśli jest otwarty
                if (marker.isPopupOpen()) {
                    marker.setPopupContent(this._flightPopupContent(f));
                }
            } else {
                const marker = L.marker([lat, lon], {
                    icon: this._planeIcon(color, f.id),
                    zIndexOffset: 1000,
                }).addTo(this.map);

                marker._atcData = f;
                // Popup czyta zawsze z marker._atcData — nigdy nie będzie stale
                marker.bindPopup(() => this._flightPopupContent(marker._atcData), { maxWidth: 280 });

                this._flightMarkers[f.id] = marker;
            }
        });
    }

    /** Ikona samolotu (div icon z kolorem agenta) */
    _planeIcon(color, flightId) {
        return L.divIcon({
            className: '',
            html: `<div class="plane-marker" style="color:${color}" title="${flightId}">✈</div>`,
            iconSize:   [24, 24],
            iconAnchor: [12, 12],
        });
    }

    /** Treść popup-u samolotu */
    _flightPopupContent(f) {
        const color = this._voivColors[f.actual_voivodeship] || "#3498db";
        return `
            <div class="flight-popup">
                <div class="fp-header" style="border-left:4px solid ${color}">
                    <b>✈ ${f.id}</b>
                </div>
                <table class="fp-table">
                    <tr><td>Trasa</td><td><b>${f.starting_point} → ${f.destination}</b></td></tr>
                    <tr><td>Wieża</td><td><span style="color:${color}">⬤</span> ${f.actual_voivodeship || "—"}</td></tr>
                    <tr><td>Prędkość</td><td>${f.speed} km/h</td></tr>
                    <tr><td>Pułap</td><td>${f.height} m</td></tr>
                    <tr><td>Status</td><td>${f.state}</td></tr>
                </table>
            </div>`;
    }

    /** Zaktualizuj przezroczystość wypełnienia województw wg liczby lotów */
    _updateVoivOpacity(voivData) {
        const maxCount = Math.max(1, ...voivData.map(v => v.aircraft_count));
        voivData.forEach(v => {
            const layer = this._voivPolygons[v.name];
            if (!layer) return;
            const opacity = v.aircraft_count > 0
                ? 0.12 + (v.aircraft_count / maxCount) * 0.28
                : 0.04;
            layer.setStyle({ fillOpacity: opacity });
        });
    }

    // ------------------------------------------------------------------
    // Panele boczne
    // ------------------------------------------------------------------

    _renderAirportPanel(airport, voivodeshipName) {
        if (!this.panel || !this.content) return;

        this.content.innerHTML = `
            <h2 style="border-bottom: 2px solid #3498db; padding-bottom: 10px;">${airport.name}</h2>
            <p style="font-size: 1.1em; color: #3498db;">
                <b>IATA:</b> ${airport.iata_code} | <b>Wojew.:</b> ${voivodeshipName}
            </p>
            <div style="margin-top: 15px; line-height: 1.8;">
                <p>📍 <b>Szerokość:</b> ${airport.location.latitude}</p>
                <p>📍 <b>Długość:</b> ${airport.location.longitude}</p>
                <hr style="border: 0.5px solid #455a64; margin: 10px 0;">
                <p>🛫 <b>Pasów startowych:</b> ${airport.infrastructure.runways_count}</p>
                <p>🅿️ <b>Miejsca postojowe:</b> ${airport.infrastructure.parking_stands}</p>
                <hr>
            </div>
        `;

        this.panel.classList.add('active');
        this.map.flyTo([airport.location.latitude, airport.location.longitude], 7);
    }

    /** Otwiera panel z informacjami o agencie / wieży województwa */
    async _showAgentPanel(voivKey) {
        if (!this.panel || !this.content) return;
        this._highlightVoivodeship(voivKey);

        const color = this._voivColors[voivKey] || "#3498db";

        // Pobierz loty i log równolegle
        let flights = [], logData = { log: [] };
        try {
            [flights, logData] = await Promise.all([
                ATC_API.getFlightsByVoivodeship(voivKey),
                ATC_API.getVoivodeshipLog(voivKey, 10),
            ]);
        } catch (e) { /* API może nie być jeszcze dostępne */ }

        const flightRows = flights.length
            ? flights.map(f => `
                <tr>
                    <td><b>${f.id}</b></td>
                    <td>${f.starting_point} → ${f.destination}</td>
                    <td>${f.speed} km/h</td>
                    <td>${f.height} m</td>
                </tr>`).join("")
            : `<tr><td colspan="4" style="color:#888">Brak aktywnych lotów</td></tr>`;

        const logRows = logData.log.slice(-5).reverse().map(line =>
            `<div class="log-entry">${line}</div>`
        ).join("") || '<div class="log-entry" style="color:#888">Brak wpisów</div>';

        this.content.innerHTML = `
            <h2 style="border-left:5px solid ${color}; padding-left:10px; text-transform:uppercase;">
                🗼 ${voivKey.replace(/_/g, " ")}
            </h2>
            <p style="color:${color}; font-weight:600;">Aktywne loty: ${flights.length}</p>

            <table class="agent-table">
                <thead>
                    <tr><th>ID</th><th>Trasa</th><th>Prędkość</th><th>Pułap</th></tr>
                </thead>
                <tbody>${flightRows}</tbody>
            </table>

            <h3 style="margin-top:16px; color:#555;">📋 Ostatnie zdarzenia</h3>
            <div class="log-container">${logRows}</div>
        `;

        this.panel.classList.add('active');
    }

    /** Podświetla wybrane województwo, resetuje poprzednie */
    _highlightVoivodeship(voivKey) {
        if (this._selectedVoiv && this._voivPolygons[this._selectedVoiv]) {
            this._voivPolygons[this._selectedVoiv].setStyle({ weight: 2, color: "#2c3e50" });
        }
        this._selectedVoiv = voivKey;
        if (this._voivPolygons[voivKey]) {
            this._voivPolygons[voivKey].setStyle({ weight: 3.5, color: this._voivColors[voivKey] || "#3498db" });
            this._voivPolygons[voivKey].bringToFront();
        }
    }
}


const mapRadar = new MapManager('map');
mapRadar.init();

