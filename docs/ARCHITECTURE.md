# Architecture - and where each part of the write-up lives in the code

Section numbers refer to *Saarthi 2.0 - Regional-Language AI Farmer Advisory & Voice
Assistant* (InduX 5.0, Problem Statement 16).

## Section 4 - the separation that matters

| Write-up layer | In the chain? | Code |
|---|---|---|
| Communication channels | No | `routers/chat.py` (dashboard, WhatsApp-ready), `routers/calls.py` (voice) |
| Conversational AI | No | `conversation/manager.py`, `conversation/intent.py` |
| LLM | No | `llm/mistral_client.py` |
| RAG | No | `rag/pipeline.py`, `rag/store.py`, `rag/embeddings.py` |
| Central orchestrator | No | `orchestrator.py` |
| **15 agricultural agents** | **Yes** | `agents/soil.py`, `crop.py`, `environment.py`, `protection.py`, `economy.py`, `validator.py` |
| Sensor transport | No (quality is Agent 1's job) | `routers/sensors.py`, `sensors/validation.py` |
| Response delivery | No | `speech/azure_speech.py`, `telephony/n8n_client.py` |

`agents/registry.py::assert_fifteen()` runs at import and in the suite: the chain cannot
quietly become 14 or 16 agents.

## Section 5 - request flow, as implemented

```
POST /api/chat  (or /api/calls/turn for a phone call)
  |
  conversation.manager.load()            session, language, farm, memory
  conversation.manager.resolve_turn()    intent + entities, carrying context forward
  |
  Orchestrator._select_agents()          intent -> agents, then pull in their dependencies
  Orchestrator._gather()                 ONE parallel fetch: RAG + weather + SoilGrids +
  |                                      market + the latest validated sensor reading
  Orchestrator._execute()                dependency waves; independent agents run together
  |                                      Agent 15 always runs last, over the others' output
  Orchestrator._compose()                Mistral writes the answer from the validated
  |                                      findings; without a key the rule engine does, and
  |                                      the reply says which one wrote it
  storage.save_advisory()                full trace: agents, evidence, warnings, provenance
```

Why gather once and run in waves: a farmer on a feature phone is paying for silence. Agents
5, 2 and 4 have no dependency on each other, so they run concurrently; 7 waits for 4 and 5
because irrigation needs the stage coefficient and the forecast.

## Section 7 - shared capabilities

* **Conversational AI** keeps turn-taking, the remembered crop and stage, pending
  clarifications, and the language. It also normalises Hinglish ("paani", "khad", "bhav")
  into English before retrieval, because the knowledge base is English.
* **LLM** is used for language, not agronomy: the prompt hands it validated agent findings
  and retrieved passages and forbids inventing numbers. Values, units and dates pass
  through unchanged.
* **RAG** is collect -> clean -> chunk (900 chars, 150 overlap) -> embed -> index ->
  hybrid search (cosine + IDF-weighted lexical + title bonus) -> topic filter -> sanitise.
  Retrieved text is untrusted: instruction-shaped lines are stripped before the prompt.

## Section 8 - the 15 agents

Each agent is a class with `agent_id`, `purpose`, `inputs`, `outputs`, `depends_on` and one
`analyse(ctx)` returning an `AgentResult`: findings, actions, data, assumptions, warnings,
missing inputs, questions, evidence, provenance, severity, confidence.

Agents are rule, calculation and retrieval driven - fifteen separate LLMs are not required
and would make the numbers irreproducible. The arithmetic that matters is explicit:

* Agent 6 - P2O5 from DAP first, N balance from urea, K2O from MOP, with grades stated.
* Agent 7 - depletion against field capacity and wilting point for the soil texture, ETc
  from ET0 x Kc, minus forecast rain, divided by the method's application efficiency.
* Agent 3 - additive score with every contribution printed as a reason.
* Agent 13 - break-even yield and price from the farmer's own cost and the named market price.

## Section 10 - sensor path

`sensors/validation.py` is the single definition of "valid", shared by the real ingest
endpoint, the simulator and Agent 1. Out-of-range values are dropped rather than clamped,
missing fields are listed, staleness is timed, and `mode` plus `provenance` travel with
every record into the agents, the advisory, the dashboard and the stored report.

Simulation covers the ten scenarios the write-up asks to be testable and is reproducible
from a seed - which is what lets the test-suite assert on sensor behaviour at all.

## Crop disease model (addition to the write-up's Agent 8)

The write-up left image diagnosis open ("images if available"). It is now a trained
classifier, and the split of responsibility is deliberate:

```
photo -> app/vision/classifier.py          ONNX EfficientNet-B0, 32 classes
            temperature-scaled softmax     calibrated on the validation split
            confidence threshold           below it: "undecided", no disease named
         -> entities.image_model_label     handed to the orchestrator as evidence
         -> Agent 8                        ranks it with the farmer's description
         -> Agent 11                       still refuses any product, dose or schedule
         -> Agent 15                       counts it in provenance, flags escalation
```

The model is **evidence, not a verdict**. It never short-circuits the chain: a confident
prediction raises Agent 8's confidence and appears first in its candidate causes, but the
agent still returns `needs_input` and asks what the rest of the plant looks like.

Training lives in `ml/` and never ships: the server carries a 16 MB ONNX graph and
onnxruntime instead of PyTorch. `ml/validate_app_inference.py` re-measures accuracy
through the serving code path, which is the only way to catch a preprocessing drift
between training and production.

## Section 12 - privacy and safety, in code

* Inbound call and sensor endpoints require `X-Saarthi-Token` / `X-Device-Token`; an unset
  secret means reject, not allow.
* Twilio credentials live in n8n; Saarthi holds none.
* Audio files are served only from the cache directory, with path traversal refused.
* Retrieved documents are sanitised against prompt injection before reaching the model.
* Every external service has a timeout, a retry policy where retrying helps, and an explicit
  "unavailable" shape - no silent fallback to a stale or invented value.

## Section 14 - testing

125 offline tests. The LLM, every HTTP service and the n8n poster are stubbed, so a failure
means the pipeline changed behaviour, not that the weather did.
