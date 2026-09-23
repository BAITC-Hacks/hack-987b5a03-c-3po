# Phase 5 frontend

React, Vite, TypeScript, and Cytoscape.js interface for the bundled AML Agent workflow. All run results come from the Phase 4 API; the browser does not calculate roles, scores, clusters, or verification.

## Run locally

Start the API from the repository root using the setup in [docs/API.md](../docs/API.md):

```bash
python -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Then start the UI in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Vite proxies `/health` and `/api` to port 8000. `npm run build` runs the TypeScript check and creates static assets. The production Nginx configuration proxies the same routes to the backend service.

## Workflow

1. Click **Start bundled demo**. The UI creates a run with logical dataset ID `bundled`, opens the safe SSE trace, and starts execution.
2. Follow persisted status and tool events while deterministic analytics, case creation, export, and verification run.
3. After independent verification passes, inspect the top 20 assessments, cluster summaries, a directed one or two hop ego graph, and node evidence. The Phase 4 API intentionally serves analytical read endpoints only for completed, verified runs.
4. Download the three CSV files and audit record. Downloads are enabled only for a completed run with `verification_status=passed`.
5. Use **Reset demo** after the run ends. Reset removes demo-owned server state, and the UI clears its stored run ID.

The UI keeps only the current demo run ID in browser session storage. A refresh reconnects to persisted status and replays safe events. GIDs stay decimal strings throughout the browser, including node search and Cytoscape element IDs.

The HTTP and SSE contract is [docs/API.md](../docs/API.md), with response models in [`backend/app/api/schemas.py`](../backend/app/api/schemas.py). The client adapter is [`src/api.ts`](src/api.ts).
