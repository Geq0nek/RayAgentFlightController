/**
 * api.js — REST API client for the ATC system.
 *
 * All methods return Promise.
 * BASE_URL points to Flask backend (port 5001 via docker-compose).
 */

const BASE_URL = "http://localhost:5001/api";

const ATC_API = {

    /** Aggregate network status: tick, sim_time, number of flights */
    async getStatus() {
        const r = await fetch(`${BASE_URL}/status`);
        return r.json();
    },

    /** List of voivodeships with number of tracked flights */
    async getVoivodeships() {
        const r = await fetch(`${BASE_URL}/voivodeships`);
        return r.json();
    },

    /** All active flights in the network */
    async getAllFlights() {
        const r = await fetch(`${BASE_URL}/flights`);
        return r.json();
    },

    /** Flights tracked by a specific tower (key in ASCII-lowercase) */
    async getFlightsByVoivodeship(voivodeship) {
        const r = await fetch(`${BASE_URL}/flights/${voivodeship}`);
        return r.json();
    },

    /** Last n tower log entries */
    async getVoivodeshipLog(voivodeship, n = 50) {
        const r = await fetch(`${BASE_URL}/log/${voivodeship}?n=${n}`);
        return r.json();
    },

    /** Last n tick reports from the entire network */
    async getTickReports(n = 30) {
        const r = await fetch(`${BASE_URL}/reports?n=${n}`);
        return r.json();
    },

    /** List of IATA airport codes */
    async getAirports() {
        const r = await fetch(`${BASE_URL}/airports`);
        return r.json();
    },

    /** Manual flight spawn */
    async spawnFlight(start, dest) {
        const r = await fetch(`${BASE_URL}/spawn`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ start, dest }),
        });
        return r.json();
    },

    /** Start simulation */
    async startSimulation() {
        const r = await fetch(`${BASE_URL}/control/start`, { method: "POST" });
        return r.json();
    },

    /** Stop simulation */
    async stopSimulation() {
        const r = await fetch(`${BASE_URL}/control/stop`, { method: "POST" });
        return r.json();
    },
};
