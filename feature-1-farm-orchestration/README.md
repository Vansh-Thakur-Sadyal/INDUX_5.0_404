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

The text assistant is available across all feature pages shown in the
navigation. It retrieves relevant entries from a local feature knowledge
index using sparse TF-IDF vectors and BM25, then grounds its answer in those
entries and any crop or soil readings supplied by the page. If the Mistral key
is unavailable, it returns retrieved feature information directly.
Current-weather questions use Open-Meteo when GPS coordinates or a town/district
are available. Current mandi prices are only quoted when the live Data.gov.in
feed returns matching records; built-in prices are sample/reference data and
are never described by chat as live. The live feed currently has no verified
market coordinates, so GPS cannot guarantee the nearest live mandi. This
feature-only project has no account registration/profile store; location is
shared only after browser GPS permission or when the user types a place.

Plant Growth tracks crop measurements, compares uploaded photos, and keeps
recent analysis history in the browser. Other included tools cover NPK advice, yield calculations, orchestration reports, input costs, weather, farm finance, farmer education, and agricultural news. The navigation also includes the Government Subsidies portal, Water Stress analysis, and Farm Photo Inspector.

Copy `.env.example` to `.env` and add a Mistral API key to enable generated
chat responses and recommendations. Feature retrieval and its grounded
knowledge responses work without a key. The optional image-classification
dataset and TensorFlow are not included in this feature-only package.
