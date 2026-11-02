# Feature 1: Farm Dashboard and Agent Orchestration

This folder contains only the farm dashboard and its backend agent pipeline.

## Run locally

From this folder in the VS Code terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run_app.py
```

Open http://127.0.0.1:8000/index.html. The backend API documentation is at
http://127.0.0.1:8001/docs. Press Ctrl+C in the terminal to stop both servers.

The dashboard also includes Mandi Market Details and Farm Support pages from
the top navigation. Market prices use the built-in Agmarknet dataset by
default; optional live Data.gov.in prices require `DATA_GOV_API_KEY` in the
backend's root `api_keys.txt` file. Farm Support uses your browser location
when available and falls back to the regional sample location. Its current
provider cards are sample listings, not a live business directory.

Copy `.env.example` to `.env` and add a Mistral API key to enable LLM-powered
chat and recommendations. The dashboard and rule-based analysis can be opened
without a key. The optional image-classification dataset and TensorFlow are not
included in this feature-only package.
