# Ray Agent Flight Controller

Rozproszony symulator kontroli ruchu lotniczego nad Polską, zbudowany na aktorach Ray. System dzieli przestrzeń na województwa, uruchamia osobnego aktora-kontrolera dla każdego regionu, przekazuje samoloty między sąsiadującymi aktorami i zapisuje komunikację agentów do PostgreSQL.

## Najważniejsze Funkcje

- Live radar z mapą Polski, markerami wież i samolotów.
- Samoloty poruszają się po trasie.
- 16 aktorów `VoivodeshipActor`, po jednym dla każdego województwa.
- Handoff samolotów między sąsiadującymi województwami.
- Warstwa `NeighborInfoService`, która przechowuje najnowsze snapshoty sąsiadów.
- Strukturalne logowanie zdarzeń agentów do PostgreSQL.
- Historia lotów z filtrowaniem logów po wielu wartościach naraz.
- pgAdmin do podglądu tabeli `agent_logs`.
- REST API dla radaru, historii, sterowania symulacją i ręcznego tworzenia lotów.

## Architektura

```text
Client UI
  |
  | HTTP /api/*
  v
Flask API
  |
  v
ATCManager (Ray actor)
  |-- SimulationClock
  |-- NeighborInfoService
  |-- DatabaseLogService -> PostgreSQL
  |
  +-- VoivodeshipActor x16
        |
        +-- handoff do sąsiednich aktorów
```

Główne komponenty:

- `ATCManager` nadzoruje sieć aktorów, ticki symulacji, generowanie lotów i API-query.
- `VoivodeshipActor` zarządza samolotami w swoim województwie.
- `SimulationClock` utrzymuje wspólny czas logiczny symulacji.
- `NeighborInfoService` przechowuje aktualne snapshoty aktywności aktorów.
- `DatabaseLogService` zapisuje strukturalne logi do PostgreSQL.
- Frontend pokazuje radar, panele wież i historię logów.

## Uruchomienie

Wymagania:

- Docker
- Docker Compose

Start całego systemu:

```bash
docker compose up --build
```

Albo w tle:

```bash
docker compose up --build -d
```

Adresy:

- Frontend: [http://localhost:8080](http://localhost:8080)
- Backend API: [http://localhost:5001](http://localhost:5001)
- Ray Dashboard: [http://localhost:8265](http://localhost:8265)
- pgAdmin: [http://localhost:5050](http://localhost:5050)
- PostgreSQL z hosta: `localhost:5433`

## pgAdmin I Baza Danych

Logowanie do pgAdmin:

```text
Email: admin@example.com
Hasło: admin
```

Połączenie z bazą w pgAdmin jest przygotowane jako:

```text
ATC Logs PostgreSQL
```

Jeśli pgAdmin poprosi o hasło do bazy:

```text
Hasło DB: atc
```

Parametry połączenia z wnętrza Dockera:

```text
Host: postgres
Port: 5432
Database: atc_logs
Username: atc
Password: atc
```

Parametry połączenia z lokalnego programu na komputerze:

```text
Host: localhost
Port: 5433
Database: atc_logs
Username: atc
Password: atc
```

Tabela z logami:

```text
public.agent_logs
```

Dane PostgreSQL są utrwalane w Docker volume:

```text
rayagentflightcontroller_atc_postgres_data
```

Zwykłe zatrzymanie kontenerów nie usuwa danych:

```bash
docker compose down
```

Dane znikną dopiero po usunięciu volume:

```bash
docker compose down -v
```

## Historia I Logi

Zakładka `Historia Lotów` pokazuje logi zapisane w PostgreSQL. Filtry obsługują wiele zaznaczeń naraz dla:

- źródła,
- celu,
- typu zdarzenia,
- ID lotu.

Pola `Od ticka`, `Do ticka`, `Tekst` i `Limit` działają jako dodatkowe zawężenia wyniku.

Przykładowe typy zdarzeń:

- `AIRCRAFT_SPAWNED`
- `AIRCRAFT_TRACKED`
- `HANDOFF_REQUESTED`
- `HANDOFF_COMPLETED`
- `AIRCRAFT_ACCEPTED`
- `AIRCRAFT_ARRIVED`
- `NEIGHBOR_ACTIVITY_REFRESHED`
- `NEIGHBOR_SNAPSHOT_PUBLISHED`
- `TICK_STARTED`
- `TICK_COMPLETED`

## API

Najważniejsze endpointy:

```text
GET  /api/status
GET  /api/flights
GET  /api/flights/<voivodeship>
GET  /api/voivodeships
GET  /api/neighbors/<voivodeship>
GET  /api/reports?n=30
GET  /api/log/<voivodeship>?n=50
GET  /api/logs
GET  /api/logs/options
GET  /api/logs/types
GET  /api/logs/status
POST /api/spawn
POST /api/control/start
POST /api/control/stop
GET  /api/airports
```

Przykład ręcznego utworzenia lotu:

```bash
curl -X POST http://localhost:5001/api/spawn \
  -H "Content-Type: application/json" \
  -d '{"start":"WAW","dest":"KRK"}'
```

Przykład filtrowania logów po kilku typach:

```bash
curl "http://localhost:5001/api/logs?event_type=HANDOFF_REQUESTED&event_type=AIRCRAFT_SPAWNED&limit=20"
```

Przykład filtrowania po kilku polach:

```bash
curl "http://localhost:5001/api/logs?source=mazowieckie&source=manager&target=lodzkie&event_type=HANDOFF_REQUESTED&limit=20"
```

## Struktura Projektu

```text
.
├── client/
│   ├── index.html
│   ├── js/
│   │   ├── api.js
│   │   ├── app.js
│   │   └── map_manager.js
│   └── style/
│       └── style.css
├── server/
│   ├── main.py
│   ├── api/
│   │   └── routes.py
│   ├── agents/
│   │   ├── actor.py
│   │   ├── manager.py
│   │   ├── neighbor_info_service.py
│   │   ├── database_log_service.py
│   │   ├── flight_engine.py
│   │   ├── aircraft_generator.py
│   │   └── topology.py
│   └── scripts/
│       └── init_logs_db.py
├── docker/
│   └── pgadmin/
│       └── servers.json
├── docs/
├── docker-compose.yml
└── README.md
```

## Przydatne Komendy

Start:

```bash
docker compose up --build -d
```

Logi backendu:

```bash
docker compose logs -f server
```

Restart backendu:

```bash
docker compose restart server
```

Inicjalizacja tabeli logów ręcznie:

```bash
docker compose run --rm server python scripts/init_logs_db.py
```

Zatrzymanie:

```bash
docker compose down
```

Pełne czyszczenie razem z danymi PostgreSQL:

```bash
docker compose down -v
```

## Uwagi Implementacyjne

- Manager nie powinien bez potrzeby odpytywać każdego aktora o stan. Do agregacji wykorzystuje snapshoty publikowane przez `NeighborInfoService`.
- Handoff przenosi własność samolotu między aktorami i jest zapisywany jako osobne zdarzenia w `agent_logs`.
- Frontend odświeża radar cyklicznie i aktualizuje otwarty panel wieży bez ponownego klikania.
- Dane logów są przechowywane w PostgreSQL jako kolumny filtrowalne oraz `payload JSONB` z dodatkowymi szczegółami zdarzenia.
