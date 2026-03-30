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

            L.geoJSON(data, {
                style: {
                    color: "#2c3e50",
                    weight: 2,
                    fillColor: "transparent"
                }
            }).addTo(this.map);
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
                airports.forEach(airport => {
                    const marker = L.marker([airport.location.latitude, airport.location.longitude], { icon: this.towerIcon })
                        .addTo(this.map);

                    marker.on('click', () => {
                        this._renderAirportPanel(airport, voivodeship);
                    });
                });
            }
            console.log("Airports loaded.");
        } catch (error) {
            console.error("Error loading airports:", error);
        }
    }

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
}


const mapRadar = new MapManager('map');
mapRadar.init();
