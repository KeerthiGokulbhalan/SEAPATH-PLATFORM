# SeaPath Platform

A maritime voyage-planning and fleet-monitoring application for academic demonstration. The platform combines route comparison, vessel management, independent voyage simulation and performance analytics.

> **Simulation notice:** Vessel movement, sea-state conditions, route risk, fuel consumption and emissions are simulated estimates. This application is not a navigational aid and must not be used for real-world voyage decisions.

## Architecture

- **Backend:** Python 3.10+, FastAPI, SQLite and Pydantic.
- **Routing:** Three A* route searches over an ocean/land grid using the `global-land-mask` dataset. Fastest, fuel-efficient and safest strategies use different environmental-risk costs.
- **Frontend:** React 18, TypeScript, Vite, React Leaflet, OpenStreetMap tiles, Recharts and Lucide icons.
- **Persistence:** SQLite stores vessel profiles and voyage records, including each voyage's own route, progress and simulation metrics.

## Run locally

### Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API documentation: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` requests to the backend on port 8000.

## Implemented workflows

1. **Fleet:** create, inspect and remove vessel profiles with persistent specifications.
2. **Plan Voyage:** choose a vessel and Indian source/destination ports; calculate three route options with distance, time, fuel, CO₂, risk and adverse-weather exposure.
3. **Current Voyage:** start a strategy selected by the operator, view only that voyage's route and current simulated position, and advance the demo simulation.
4. **Fleet tracking:** overview map can show independently stored active voyage positions.
5. **Weather alerts and rerouting:** deterministic simulated risk segments are attached to the selected route. A high-severity segment approaching the vessel can trigger one automatic safest-route diversion.
6. **Voyage lifecycle:** active voyages can be interrupted; arrived and interrupted voyages stop advancing.
7. **Analytics:** overall totals and a per-voyage planned-versus-simulated report are derived from the correct stored voyage record.

## API

- `GET /api/health`, `GET /api/ports`
- `GET/POST /api/vessels`, `DELETE /api/vessels/{id}`
- `POST /api/routes/plan`
- `GET /api/voyages`, `POST /api/voyages`, `GET /api/voyages/{id}`
- `POST /api/voyages/{id}/tick?minutes=10`, `POST /api/voyages/{id}/interrupt`
- `GET /api/analytics`, `GET /api/voyages/{id}/analytics`

## Validation

Run backend tests from the backend directory:

```bash
python -m pytest -q
```

The GitHub Actions workflow runs backend tests and a frontend production build on pushes and pull requests.

## Notes and limitations

- The route engine uses a gridded land mask and coordinate-derived weather scenarios. It is suitable for repeatable academic demonstrations, not chart-grade navigation.
- The supplied port dataset focuses on Indian ports. Add verified port coordinates before expanding to other regions.
- OpenStreetMap basemap tiles and Google Fonts require an internet connection in the browser.
- For deployment, restrict CORS, configure HTTPS and authentication, use a managed database, and replace demo environmental inputs with validated operational data.


CI validation branch: automated backend and frontend checks are being run against this revision before merge.
