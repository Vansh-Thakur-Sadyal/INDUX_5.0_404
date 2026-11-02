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

Copy `.env.example` to `.env` and add a Mistral API key to enable LLM-powered
chat and recommendations. The dashboard and rule-based analysis can be opened
without a key. The optional image-classification dataset and TensorFlow are not
included in this feature-only package.
