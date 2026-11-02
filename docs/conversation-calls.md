# The conversational call channel

A farmer rings the helpline and has a conversation: Saarthi greets them by name if it
knows them, listens, answers in one or two spoken sentences, asks one question at a
time, and remembers what was said for the next call.

```
farmer's phone
   |  ordinary voice call
Twilio  --(voice webhook)-->  n8n   (holds the Twilio credential)
                               |  POST /api/voice/turn   X-Saarthi-Token
                            Saarthi
                               |- loads this phone number's memory
                               |- small talk        -> Sarvam conversations model  (~0.5 s)
                               |- farm question     -> 15 agents, then Sarvam says it (~5 s)
                               |- extracts durable facts and stores them
                               |- Sarvam bulbul TTS -> /api/speech/audio/<hash>.wav
                               v
                             n8n -> TwiML <Play> + <Gather> -> Twilio -> farmer hears it
```

## Why Sarvam

The key supplied covers the whole voice path, and it is built for Indian languages.
Verified on this account on 2026-10-14:

| Job | Model | Checked |
|---|---|---|
| Conversation | `sarvam-105b-conversations` | natural Hinglish, keeps multi-turn context |
| Reasoning fallback | `sarvam-105b` | available (needs a larger `max_tokens`) |
| Speech out | `bulbul:v3`, 37 voices | 8 kHz WAV for telephony |
| Speech in | `saarika:v2.5` | round-tripped a Hindi sentence back exactly |

Sarvam is now the preferred speech provider app-wide; Azure stays as an alternative
and the dashboard microphone uses whichever is configured.

## Memory: what is kept, and what is not

`backend/app/core/farmers.py`, three tables keyed on the phone number (last 10 digits,
so `+91 98765 43210` and `09876543210` are one farmer):

* **farmers** - name, village, district, land acres, soil, irrigation, language, call count
* **farmer_facts** - a small allow-list of durable keys (crop, sowing_date, irrigation_method,
  problem, market, livestock...). Anything else the extractor returns is dropped, so the
  memory cannot fill with chatter.
* **call_log** - every call with its turn-by-turn transcript and a one-line summary written
  at hang-up.

Rules that matter:

* Facts come only from what the farmer **said**, each tagged with the call it came from.
* A later call corrects an earlier fact - the farmer is the authority.
* **Identity is written once.** An extractor asked for a name will find one in almost any
  sentence: it once read *bijai* (Hindi for sowing) as the farmer's name and overwrote
  "Ramesh Kumar". Name and village are only set when empty; corrections go through
  `POST /api/farmers/{phone}`.
* A farmer can have a fact deleted: `DELETE /api/farmers/{phone}/facts/{key}`.

## Endpoints n8n uses

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/voice/turn` | one turn: transcript in, reply + audio URL out. Empty transcript = greeting |
| POST | `/api/voice/end` | hang-up: writes the one-line summary the next call opens with |
| POST | `/api/voice/transcribe` | audio in, text out (for channels that send audio) |
| GET | `/api/voice/status` | which models and provider are live, and memory counts |
| GET | `/api/farmers`, `/api/farmers/{phone}` | the stored profile, facts and call history |

All inbound calls require `X-Saarthi-Token`. An unset secret means *reject*, never *allow*.

## Live wiring (done 2026-10-14)

| Piece | Value |
|---|---|
| Twilio number | **+1 415 449 7491** (voice enabled, Full account, not trial) |
| Twilio voice webhook | `https://n8n-kmar.srv1804200.hstgr.cloud/webhook/saarthi-conv-answer` |
| Twilio status callback | `.../webhook/saarthi-conv-ended` |
| n8n workflow | `Saarthi_Conversation_Agent`, id `SaarthiConv00001`, **active** |
| Twilio credential | n8n credential `Twilio account` (`jX9mHOCEiA0ugAqo`) - verified by executing a throwaway workflow that did a harmless `GET /Accounts/{sid}.json` |
| Saarthi backend | `https://indux5.0.visioncoders.in` |

The auth token is **only** in n8n's encrypted credential store. `config/api_keys.env`
holds the Account SID (an identifier the REST URL needs) and the helpline number.

Rebuild the importable workflow after any template change:

```bash
python deploy/build_n8n_workflow.py          # writes deploy/generated/ (gitignored)
```

It refuses to emit a file that still contains a placeholder, and it re-attaches the
Twilio credential binding - `n8n import:workflow` replaces the node array and would
otherwise wipe it.

## Setting up the n8n chain

Import `docs/n8n/saarthi_conversation_workflow.json`. It has four webhooks on one canvas:

| Webhook | Called by | Does |
|---|---|---|
| `saarthi-conv-answer` | Twilio, when the call connects | greeting + opens the mic |
| `saarthi-conv-turn` | Twilio `<Gather>` | every farmer utterance -> Saarthi -> speak -> listen again |
| `saarthi-conv-ended` | Twilio status callback | writes the call summary |
| `saarthi-conv-callback` | the dashboard | places an outbound call through Twilio |

Then: set the four **Config - EDIT ME** nodes (Saarthi base URL, the inbound token, your
n8n host, Twilio SID and number), attach the `twilioApi` credential to *Twilio: place the
call*, activate, and point your Twilio number's "A call comes in" at the
`saarthi-conv-answer` production URL.

`speechTimeout: "auto"` in the Gather is what makes it feel like a conversation rather
than an IVR - Twilio stops listening when the farmer stops talking.

## Verified through the real chain (2026-10-14)

Posting Twilio's own form to the live n8n webhook returns playable TwiML:

```
POST /webhook/saarthi-conv-answer   CallSid=CAdemo01&From=+919812345678
-> <Gather input="speech" speechTimeout="auto" language="hi-IN" ...>
     <Play>https://indux5.0.visioncoders.in/api/speech/audio/dd211a77ea53b236a17afe94.wav</Play>
   [http 200 in 1.7 s]   audio fetches 200, audio/wav, 87 KB

POST /webhook/saarthi-conv-turn     SpeechResult=Aapko yaad hai mere khet me kya laga hai
-> "Haan ji Suresh bhai, mujhe yaad hai aapke teen ekad mein sarson lagi hai.
    Kya aaj uski koi dikkat aa rahi hai?"
   [http 200 in 2.7 s]   audio fetches 200, audio/wav, 137 KB
```

**A bug this caught:** the greeting branch returned a *relative* audio path. Twilio
fetches `<Play>` from its own servers, so a real call would have played silence while
every local test passed. Both branches now return absolute URLs, and a test asserts it.

## Measured behaviour (live on the VPS, 2026-10-14)

Call 1 from a number never seen:

> **Farmer:** Namaste, mera naam Suresh Yadav hai, main Barsana se bol raha hoon
> **Saarthi:** Namaste Suresh ji. Barsana se baat karke accha laga. Aap bataiye, aaj kheti ka kya kaam chal raha hai?
> *[remembered name=Suresh Yadav, village=Barsana | 453 ms]*

Call 2, same number:

> **Farmer:** Aapko yaad hai mere khet me kya laga hai?
> **Saarthi:** Haan ji, teen ekad mein sarson lagi hai na aapki? Abhi ismein koi dikkat aa rahi hai kya?
> *[returning caller, 2 facts recalled | 401 ms]*

> **Farmer:** Sarson me paani kab dena chahiye?
> **Saarthi:** Suresh ji, mitti mein nami 30.6% hai jo sarson ke liye bilkul sahi hai... hawa 7.6 kmph chal rahi hai, toh abhi spray mat kijiyega.
> *[agents 1, 4, 5, 2, 7, 15 | 4.8 s]*

Conversational turns land in **0.4-0.6 s**; a turn that needs the agent chain takes
**~5 s**, because it is fetching live weather and running the 15 agents. The n8n flow can
cover that with a short filler line if it feels long on a real call.

## Limits

* Barge-in (interrupting Saarthi mid-sentence) is not implemented - Twilio `<Gather>`
  listens after the reply finishes. True duplex needs Twilio Media Streams.
* The fact extractor runs on the farmer's own words only. It will miss things said
  indirectly, and that is deliberate: a missed fact is cheap, an invented one is not.
