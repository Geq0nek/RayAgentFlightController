/**
 * api.js — klient REST API dla systemu ATC.
 *
 * Wszystkie metody zwracają Promise.
 * BASE_URL wskazuje na Flask backend (port 5001 przez docker-compose).
 */

const BASE_URL = "http://localhost:5001/api";

const ATC_API = {

    /** Zbiorczy status sieci: tick, sim_time, liczba lotów */
    async getStatus() {
        const r = await fetch(`${BASE_URL}/status`);
        return r.json();
    },

    /** Lista województw z liczbą śledzonych lotów */
    async getVoivodeships() {
        const r = await fetch(`${BASE_URL}/voivodeships`);
        return r.json();
    },

    /** Wszystkie aktywne loty w sieci */
    async getAllFlights() {
        const r = await fetch(`${BASE_URL}/flights`);
        return r.json();
    },

    /** Loty śledzone przez konkretną wieżę (klucz ASCII-lowercase) */
    async getFlightsByVoivodeship(voivodeship) {
        const r = await fetch(`${BASE_URL}/flights/${voivodeship}`);
        return r.json();
    },

    /** Ostatnie n wpisów logu wieży */
    async getVoivodeshipLog(voivodeship, n = 50) {
        const r = await fetch(`${BASE_URL}/log/${voivodeship}?n=${n}`);
        return r.json();
    },

    /** Ostatnie n tick-raportów całej sieci */
    async getTickReports(n = 30) {
        const r = await fetch(`${BASE_URL}/reports?n=${n}`);
        return r.json();
    },

    /** Lista kodów IATA lotnisk */
    async getAirports() {
        const r = await fetch(`${BASE_URL}/airports`);
        return r.json();
    },

    /** Ręczny spawn lotu */
    async spawnFlight(start, dest) {
        const r = await fetch(`${BASE_URL}/spawn`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ start, dest }),
        });
        return r.json();
    },

    /** Uruchom symulację */
    async startSimulation() {
        const r = await fetch(`${BASE_URL}/control/start`, { method: "POST" });
        return r.json();
    },

    /** Zatrzymaj symulację */
    async stopSimulation() {
        const r = await fetch(`${BASE_URL}/control/stop`, { method: "POST" });
        return r.json();
    },
};
