# Farm RAG and Model

This folder is a portable RAG package: local feature retrieval (TF-IDF/BM25),
crop matching rules, context enrichment, response guardrails, and the Mistral
API client. It contains no API keys or database. The crop ranges are the app's
reference values, not a local agricultural extension database.

## Run the demo API

Use Python 3.10 or newer. From the project folder containing `rag_model`:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r rag_model\requirements.txt
$env:MISTRAL_API_KEY = "your-own-key"
.\.venv\Scripts\python.exe -m uvicorn rag_model.demo_api:app --host 127.0.0.1 --port 8010
```

Open `http://127.0.0.1:8010/docs` to try `POST /chat`. Example request:

```json
{
  "message": "Which app feature shows mandi prices?",
  "farm_context": {
    "nitrogen": 42,
    "phosphorus": 28,
    "potassium": 55,
    "npk_unit": "mg/kg"
  }
}
```

For a soil-based crop shortlist, ask a crop-selection question and include
`crop` and `farm_context` readings. `lat`, `lon`, or `location` are optional.
The demo uses Open-Meteo for location/weather enrichment when location is
provided. Do not send private farm or account data to a service unless you
intend to share it.

## Use in another Python app

Copy this entire folder into the other project, install `requirements.txt`,
then import:

```python
from rag_model.assistant import answer_question

result = answer_question(
    "Which app feature shows mandi prices?",
    body={"farm_context": {"nitrogen": 42, "phosphorus": 28, "potassium": 55}},
)
print(result["reply"])
```

Set `MISTRAL_API_KEY` in the environment. The feature index and deterministic
crop shortlist work locally; generated answers require the Mistral API.
Do not share API keys with the folder. The included feature corpus describes
this farm app and should be edited to match another app.
