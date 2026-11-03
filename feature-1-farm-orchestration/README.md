# Feature 1: Farm Dashboard and Agent Orchestration

This folder contains only the farm dashboard and its backend agent pipeline.

## Run locally

From this folder in the VS Code terminal:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_app.py
```

On Windows, you can instead double-click `run.bat` in this folder (or the
repository-root `run.bat`). It creates the Python 3.12 environment if needed,
checks/installs missing requirements, and keeps the console open to show
startup errors. Keep that window open while using the app.

Open http://127.0.0.1:8000/index.html. The backend API documentation is at
http://127.0.0.1:8001/docs. Press Ctrl+C in the terminal to stop both servers.

The dashboard also includes Mandi Market Details and Farm Support pages from
the top navigation. Built-in market records are sample/reference data, not
current quotes. Optional live Data.gov.in prices require `DATA_GOV_API_KEY` in
`api_keys\api_keys.txt` (create the file inside the `api_keys` folder). The live feed currently does not supply
verified market coordinates, so nearest-market distance cannot be guaranteed
for those records. Farm Support uses your browser location when available and
falls back to the regional sample location. Its current provider cards are
sample listings, not a live business directory.

Emergency SOS is a local prototype for recording or uploading incident voice
notes, attaching GPS coordinates, and showing reports within a 5 km radius.
Reports and audio are stored in the ignored `sos_data` folder and automatically
expire after seven days. The local launcher binds the API to `127.0.0.1`, so
other devices cannot receive these reports. A shared deployment would need
verified farmer accounts, access controls, and a real notification service.
The prototype does not contact emergency services; call 112 directly in an
emergency. Users must consent before sharing exact incident coordinates and
voice recordings.

Hardware Share is also a local prototype. It provides an in-memory shared
hardware queue, optional GPS capture with informational 5 km hub and 100 m
return-distance guidance (distances are not calculated or enforced), two
started sessions per calendar month, two-hour sessions, and a seven-day
blacklist for overdue returns. The overdue worker runs once per minute. For API testing, a
sample unit (`sample-hardware-001`) is seeded at the Gwalior demo coordinates
configured in `backend/api/hardware_share.py`; it is not a real registered
device. Bookings are available from the dashboard's Hardware Share navigation
or the floating Book Hardware shortcut. State resets when the backend restarts
and is not shared across worker processes. User endpoints require `X-User-Id`;
this prototype header is not authentication and must be replaced with a
verified account identity before deployment. The session return request also
requires the token returned by the start endpoint.

The text assistant is available across all feature pages shown in the
navigation. It retrieves relevant entries from a local feature knowledge
index using sparse TF-IDF vectors and BM25, then grounds its answer in those
entries and any crop or soil readings supplied by the page. If the Mistral key
is unavailable, it returns retrieved feature information directly.
Feature retrieval uses the user's question rather than unrelated page context.
Crop diagnosis and fertilizer dosing remain evidence-bounded. Crop-selection
questions use a deterministic ranking against the NPK Advisor's built-in crop
ranges, plus the current broad India season; missing readings fall back to a
short seasonal list and mention missing NPK inputs only when useful. These app ranges are not a
district-validated agronomy database. A recent HM-10 reading is included when
available, but current form values take priority. The assistant uses browser
GPS only when permission is granted and labels date-based season and location
assumptions. The existing sparse TF-IDF/BM25 index contains app-feature
descriptions, not a crop-agronomy corpus or similarity scores; crop advice
therefore uses exact local crop-reference lookup and the deterministic rules
engine rather than pretending to perform vector retrieval. Comparing vegetable
prices requires a location and matching live Agmarknet records; sample records
are never reported as current.
For local weather enrichment, permitted coordinates are used to fetch
approximate locality and current/near-term weather from OpenStreetMap Nominatim
and Open-Meteo. Soil moisture, temperature, soil type, and irrigation are
included as context but are not assigned crop-fit scores by the app matrix.
Current-weather questions use Open-Meteo when GPS coordinates or a town/district
are available. Current mandi prices are only quoted when the live Data.gov.in
feed returns matching records; built-in prices are sample/reference data and
are never described by chat as live. The live feed currently has no verified
market coordinates, so GPS cannot guarantee the nearest live mandi. This
feature-only project has no account registration/profile store; location is
shared only after browser GPS permission or when the user types a place.

The reusable RAG and model code is grouped in [`rag_model/`](./rag_model/).
Copy that folder to another Python project to use the local feature index,
crop-matching rules, context builder, response guard, Mistral client, and
standalone demo API. Setup and usage are documented in
[`rag_model/README.md`](./rag_model/README.md). Do not share API keys; each
user must configure their own `MISTRAL_API_KEY`.

Plant Growth tracks crop measurements, compares uploaded photos, and keeps
recent analysis history in the browser. Other included tools cover NPK advice, yield calculations, orchestration reports, input costs, weather, farm finance, farmer education, and agricultural news. The navigation also includes the Government Subsidies portal, Water Stress analysis, and Farm Photo Inspector.

Copy `.env.example` to `.env` and add a Mistral API key to enable generated
chat responses and recommendations. Feature retrieval and its grounded
knowledge responses work without a key. The optional image-classification
dataset and TensorFlow are not included in this feature-only package.
