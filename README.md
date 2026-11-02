# Saarthi 2.0

**Regional-language AI farmer advisory and voice assistant**
InduX 5.0 - Problem Statement 16 - Theme: Multilingual AI & Language Technologies
Team VisionCoders - Team ID InduX5-26VU-404

Conversational AI + RAG + **exactly 15 agricultural agents** + 7-parameter soil sensor +
feature-phone calling, behind one dashboard and one API.

**Live: https://indux5.0.visioncoders.in** - deployed to the Hostinger VPS behind the
shared Traefik. Redeploy steps and the gotchas of that box: [deploy/DEPLOY.md](deploy/DEPLOY.md).

---

## 1. Run it (VS Code or a terminal)

```bat
run.bat
```

That creates `.venv`, installs dependencies, and serves **everything on one origin**:

| | |
|---|---|
| Dashboard | http://127.0.0.1:8010/ |
| API docs (Swagger) | http://127.0.0.1:8010/docs |
| Capability report | http://127.0.0.1:8010/api/keys/status |

In VS Code: open this folder, press **F5** and pick *Saarthi: run dashboard + API*
(`.vscode/launch.json` is set up, `.venv` is the selected interpreter).

Manually:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
cd backend
../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8010 --reload
```

Tests:

```bash
cd backend
../.venv/Scripts/python.exe -m pytest -q
```

---

## 2. Keys - `config/api_keys.env`

This is the only file you edit. **The app runs without any key** and tells you, on the
dashboard and in the API, exactly what each missing key disables. Nothing is faked to
cover a gap.

| Key | Powers | Without it |
|---|---|---|
| `MISTRAL_API_KEY` | Answer generation + RAG embeddings | Agents and RAG still run; the reply is assembled by the rule engine and says so. Replies stay in English. |
| `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` | Microphone (STT) and spoken replies (TTS) in 13 languages | Text chat works; the mic is disabled with a reason. |
| `N8N_BASE_URL` + `N8N_OUTBOUND_CALL_PATH` | Twilio calls through your n8n | Call requests are recorded as **clearly labelled simulations** and never dialled. |
| `SAARTHI_INBOUND_TOKEN` | Shared secret n8n and the Android gateway must present | Inbound call/sensor endpoints reject everything (401). |
| `DATA_GOV_API_KEY` | Live Agmarknet mandi prices | Market answers say prices are unavailable. No price is ever invented. |
| *(none needed)* | Open-Meteo weather, ISRIC SoilGrids | - |

### Status of this install (13 Oct 2026)

* **Mistral: live.** A working key is in `config/api_keys.env`. Measured against that
  account: `ministral-14b-latest` answers (default), `ministral-8b-latest` and
  `ministral-3b-latest` answer (fallback chain), `mistral-small-latest` and
  `mistral-medium-latest` return **429**, `mistral-large-latest` returns **403**. The client
  walks the chain automatically and every answer records which model wrote it
  (`advisory.answer_model`, shown as a tag in the chat).
* **Azure Speech: deliberately off.** The dashboard greys out the microphone and the
  "Speak reply" switch and says why; telephony falls back to Twilio `<Say>`. Paste a key
  and restart to enable voice.
* The older agritech project's Mistral and Azure keys are dead (**401**) - do not reuse them.

---

## 3. What is where

```
backend/app
  core/        config, 13-language registry, pydantic contracts, SQLite storage
  llm/         Mistral chat + embeddings (graceful, retrying, test-stubbable)
  rag/         loaders (PDF/Word/sheet/web), chunking, embeddings, vector store,
               hybrid retrieval, rank fusion, query rewrite, guardrails, the chain
  conversation/ intent routing, Hinglish lexicon, session + memory  <- Conversational AI
  agents/      the 15 agricultural agents + crop reference tables
  orchestrator.py   selects agents, gathers data once, runs waves, validates
  sensors/     7-parameter schema, Modbus register decode, validation, simulator
  speech/      Azure STT/TTS + browser token issuing
  telephony/   n8n client (Twilio lives in n8n, never here)
  routers/     the HTTP surface
frontend/      the dashboard (vanilla HTML/CSS/JS, no build step)
console/       the knowledge console - feed the bots, watch the chain run
data/knowledge/  18 agronomy documents - the RAG corpus it ships with
docs/          n8n + Twilio setup, Arduino sketch, architecture notes
```

### The knowledge console

Three ways in, all the same page and the same administrator session:

* **Admin > Knowledge** in the dashboard sidebar - embedded, so feeding the bots
  is part of running the server rather than a different tool
* `/kb/` on any hostname, for a full screen of it
* the root of `SAARTHI_CONSOLE_HOST` once that DNS name exists

Upload a PDF, a spreadsheet, a Word file or a web page and both bots can quote it,
with citations, within seconds. The page shows all six indexing stages as they run
and whose upload they belong to, the whole library with its state and its
contributor, and a live block diagram of the seven-stage retrieval chain running
against a question you type.

Every retrieval is logged - from the console, the dashboard chatbot and the phone
line alike - so the questions that found nothing are visible as a backlog of gaps.
Full write-up: [docs/KNOWLEDGE_CONSOLE.md](docs/KNOWLEDGE_CONSOLE.md).

### Telling it to do something, and waking it by name

A sentence can now be an instruction rather than a question. *"mandi bhav
dikhao"* opens the market page in **216 ms** — nothing is generated, the words
are fixed — while *"gehu ka bhav kya hai"* still runs the chain and answers with
a real price. Detection is by rule and never by a model, because a wrong match
has to be explainable.

**On a phone call it does them too**, and differently, because a call is not a
screen: "sensor se reading lo" gets an honest "the probe talks to the app, not to
me", "dobara boliye" repeats the same words, "english mein baat karo" switches
the call and sticks. It no longer promises to send a report it cannot send.

Say **"Saarthi"** to the dashboard in Chrome, or to the phone with the app
installed, and it wakes listening - on the phone even from the lock screen, with
the limits that implies spelled out on the toggle. Full write-up:
[docs/VOICE_ACTIONS.md](docs/VOICE_ACTIONS.md).

### Two models, each on the job it is better at

The split is measured, not chosen, and the numbers are in
[`backend/app/llm/router.py`](backend/app/llm/router.py).

| Job | Model | Measured |
|---|---|---|
| Phone conversation | `sarvam-105b-conversations` | **286 ms** vs ministral-8b's 755 ms; asked "namaste", ministral-8b replied by asking the caller who they were |
| Phone answer with figures | `sarvam-105b-conversations`, **checked** | 312 ms, and every figure it speaks is verified against what the agents produced |
| Query rewriting, Indian languages | `sarvam-105b` | "mera gehu pila ho raha hai" → *"wheat lower leaf chlorosis"*. Mistral gave *"wheat lodging"* - a different problem |
| Query rewriting, English | `ministral-8b` | half the latency, no Indic advantage to pay for |
| Answers, citations, reranking, extraction, embeddings | Mistral | citation discipline, JSON mode, and Sarvam has no embedding endpoint |
| Speech in and out | Sarvam `bulbul` / `saarika` | why it was chosen in the first place |

**Sarvam speaks; it never works anything out.** The agents compute, Sarvam says,
and `unsupported_figures` checks the spoken reply against the facts it was handed.
A reply holding a figure nobody supplied is thrown away and Mistral re-phrases it;
if both invent, the farmer is told plainly that the figure is not known yet. That
check is why the fast, fluent model can be trusted with a farmer's numbers - asked
a wheat price with no market data loaded, Sarvam answered "2200 se 2300 rupaye per
quintal", a price from nowhere.

Median spoken turn: **~400 ms**, against 748 ms when Mistral carried the
conversation. Each choice is overridable in `config/api_keys.env` without a code
change.

Dashboard answers are streamed as they are written - first words at 440 ms against
2.1 s of blank screen before - over `POST /api/chat/stream`.

### The separation the write-up asks for

| Layer | In the 15-agent chain? |
|---|---|
| Channels (dashboard, call, WhatsApp), Conversational AI, LLM, RAG, orchestrator, sensor transport, delivery | **No** - shared capabilities |
| Agents 1-15 | **Yes** |

`registry.assert_fifteen()` runs at import and in the test-suite, so the count cannot drift.

| # | Agent | # | Agent |
|---|---|---|---|
| 1 | Soil Sensor Acquisition & Data Quality | 9 | Weed Management |
| 2 | Soil Health & Interpretation | 10 | Seed, Sowing & Establishment |
| 3 | Crop Suitability & Selection | 11 | Crop Protection & Safe Application |
| 4 | Crop Growth Stage & Agronomy | 12 | Agricultural Market Intelligence |
| 5 | Weather & Climate Risk | 13 | Farm Economics & Profitability |
| 6 | Nutrient Management & Fertiliser | 14 | Sustainable Farming & Crop Residue |
| 7 | Irrigation & Water Management | 15 | Integrated Advisory & Validation |
| 8 | Pest & Disease Risk | | |

---

## 3b. Crop disease model (trained here, not bought in)

The Pest & Disease page is backed by a real classifier trained on this machine's GPU:

```
39,531 leaf images, 32 crop__condition classes
  PlantDoc 2,459 + Cassava 21,397 + Plant Pathology 2021 15,675
EfficientNet-B0, trained centrally AND federated (FedAvg over the 3 datasets as clients)
  -> the better model wins on validation macro recall
  -> exported to ONNX (~16 MB) and verified against PyTorch before shipping
  -> served with onnxruntime, so the server needs no PyTorch
```

Everything about it - how the datasets were merged, why multi-label rows were dropped,
how the confidence threshold was chosen - is in [ml/README.md](ml/README.md), and the
measured numbers live in `data/models/leaf_disease.json` (written by the export script,
never hand-edited).

Two properties matter more than the headline accuracy:

* **It refuses.** Below the measured confidence threshold the API returns "undecided"
  and names no disease. The threshold is the lowest cut-off whose accuracy-when-shown
  still clears 90%.
* **It is evidence, not a verdict.** The prediction is handed to Agent 8 as one input;
  Agent 11 still refuses to name a product or a dose, and the UI labels it a screening
  aid throughout.

`ml/validate_app_inference.py` re-scores the held-out test split through the *app's*
ONNX code path and fails if it drifts from the training measurement by more than 3
points - that is what catches a preprocessing mismatch between training and serving.

## 4. The dashboard (11 screens)

Overview - Soil Health - Weather - Pest & Disease - Market Prices -
AI Assistant - **Voice Calls** - Sensor Devices - Reports.

A floating chatbot sits on every screen with three switches: **RAG**, **15 agents**,
**Voice**, plus the language picker and a microphone button.

Every number on screen carries its provenance. A simulated reading is labelled
`SIMULATED` on the tile, in the API payload, in the agent output and in the advisory
warnings - it can never be mistaken for a measurement.

---

## 5. Calling (Twilio credentials stay in n8n)

```
farmer's phone -> Twilio -> n8n (your VPS, holds the Twilio credential)
                              |  POST /api/calls/turn   (X-Saarthi-Token)
                              v
                    Saarthi: intent -> agents -> RAG -> Agent 15 -> answer
                              |  reply text + Azure TTS audio URL
                              v
                         n8n -> Twilio <Play> -> farmer hears it
```

Endpoints Saarthi exposes to n8n:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/calls/turn` | one spoken turn: transcript or DTMF in, reply + audio URL out |
| POST | `/api/calls/status-callback` | Twilio call status forwarded by n8n |
| GET | `/api/calls/ivr/languages` | the keypad language menu (1 English, 2 Hindi, ...) |
| POST | `/api/calls/outbound` | the dashboard asks n8n to dial a farmer back |
| POST | `/api/calls/simulate` | runs a full call turn locally, labelled simulated |

Setup, with the importable workflow: **[docs/n8n-twilio-setup.md](docs/n8n-twilio-setup.md)**.

---

## 6. Sensor path

`7-in-1 RS485 Modbus probe -> Arduino Uno -> HM-10 BLE -> Android app -> POST /api/sensors/ingest`

* Reference sketch and the BLE packet format: **[docs/arduino-hm10-sketch.ino](docs/arduino-hm10-sketch.ino)**
* The register map is served at `/api/sensors/register-map` and is marked **provisional**
  until the sensor manual is confirmed. Arduino Uno is 5 V logic, most HM-10 boards are
  3.3 V - protect the HM-10 RX pin.
* Out-of-range values are **dropped, never clamped**. Stale readings are flagged. Missing
  fields are reported. Simulation covers normal, dry, saline, acidic, nutrient-poor,
  unusual, missing-fields, out-of-range, stale and disconnected, each reproducible from a
  seed.

---

## 7. Honesty rules built into the code

These are enforced by the agents and covered by tests, not just documented:

* No pesticide name, dose, dilution or spray interval is ever given (Agent 11 escalates).
* A fertiliser dose is never calculated from sensor NPK values (Agent 6 says so explicitly).
* No diagnosis from a symptom or a photo - only candidate causes and what to check (Agent 8).
* No future price, ever; market figures always carry market, date, unit and source (Agent 12).
* No cost of cultivation is invented - Agent 13 uses the farmer's own numbers.
* No environmental figure is quantified without measurement (Agent 14).
* Simulated data, stale data and missing services are surfaced in the answer (Agent 15).

---

## 8. Test suite

```
711 tests, fully offline (network, LLM and telephony are stubbed)
  sensors ........ decoding, validation, provenance, 10 simulation scenarios
  rag/intent ..... ingest, retrieval, topic filters, injection stripping, routing matrix
  knowledge ...... loaders per format, PII masking, injection refusal, grounding,
                   rank fusion, per-document diversity, the audience access rule,
                   the six ingest stages, delete taking its vectors with it
  streaming ...... no retry on a phone turn, the embedding cache, deltas that
                   reassemble into the stored answer
  fusion ......... which model takes which job, the rewriter chosen by the language
                   of the question, and the figure check: an invented price is
                   rejected and re-phrased, and with no facts at all no figure may
                   be spoken
  voice actions .. an instruction is carried out and a question is not hijacked,
                   the command queue expires and is scoped to the account, and
                   "open admin" is a sentence Saarthi does not understand
  call actions ... a caller is answered in their language, "say that again"
                   repeats the same words, switching language sticks, and the
                   bot never promises to send something it cannot send
  agents ......... all 15 contracts, fertiliser arithmetic, irrigation water balance,
                   dose refusal, conflict detection
  orchestrator ... agent selection, dependency order, LLM vs rule engine, memory
  api/telephony .. every endpoint, token auth, the full n8n call contract
```

The Android companion in `../App` has 36 of its own, run with
`gradlew testDebugUnitTest`.

---

## 9. Known limits

* Without a Mistral key, replies are English rule-engine prose and retrieval uses the
  offline hashed embedder - good enough to demo, weaker than `mistral-embed`, and labelled.
* The ministral models need the language rule repeated as the last line of the prompt or
  they mirror the farmer's Hinglish, and they will translate a crop name into a different
  crop (one wrote *turmeric* for *mustard*) unless told to keep crop names in English.
  Both are handled in `llm/mistral_client.py::language_directive` - if you change that
  prompt, re-check a Hinglish question and a non-English answer before demoing.
* Crop profiles cover 8 crops; Agent 3 and Agent 4 refuse rather than guess outside them.
* No image disease model. The Pest page stores the photo and reasons from your description.
* A scanned PDF with no text layer cannot be indexed. The console reports it as needing
  OCR rather than indexing zero characters and looking successful; no OCR engine is
  bundled.
* Query rewriting costs roughly half a second, so it is off on the phone line and
  available in the console and on the dashboard. The keyword normaliser in
  `conversation/intent.py` covers the common cases for free.
* The grounding check compares figures and word overlap. It catches a number the model
  invented; it cannot catch a sentence that is wrong but uses only words from the
  sources.
* The n8n workflow in `docs/` is written against the documented Twilio and n8n APIs; it has
  not been executed against your instance, so import it and test one call before the demo.
