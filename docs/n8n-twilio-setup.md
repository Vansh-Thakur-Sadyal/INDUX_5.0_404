# Calling: Twilio through n8n

Saarthi never stores a Twilio Account SID or auth token. Your n8n instance owns the Twilio
credential; Saarthi only exchanges JSON with n8n over two webhooks in each direction.

```
   farmer's phone
        |  (ordinary voice call - no app, no internet on the handset)
     Twilio
        |  voice webhook
      n8n  (holds credential "twilioApi")
        |  POST  <saarthi>/api/calls/turn     header X-Saarthi-Token
     Saarthi  ->  intent -> 15 agents -> RAG -> Agent 15 -> answer
        |  reply text + Azure TTS audio URL
      n8n  ->  TwiML <Play> + <Gather>  ->  Twilio  ->  farmer hears the answer
```

---

## 1. Fill in `config/api_keys.env`

```properties
N8N_BASE_URL=https://n8n-kmar.srv1804200.hstgr.cloud
N8N_OUTBOUND_CALL_PATH=/webhook/saarthi-outbound-call
N8N_CALL_STATUS_PATH=/webhook/saarthi-call-status
N8N_CALL_HANGUP_PATH=/webhook/saarthi-hangup   # optional, see "Force end call" below
N8N_WEBHOOK_TOKEN=<any secret; Saarthi sends it to n8n as X-Saarthi-Token>
SAARTHI_INBOUND_TOKEN=<any secret; n8n must send it back on every inbound call>
SAARTHI_HELPLINE_NUMBER=+91XXXXXXXXXX
SAARTHI_PUBLIC_URL=https://<public hostname of this backend>
```

`SAARTHI_PUBLIC_URL` matters: it is the base of the audio URL handed to Twilio. Twilio must
be able to reach it, so `127.0.0.1` only works if you tunnel it (`cloudflared`, `ngrok`) or
deploy the backend behind your Traefik.

Check the contract any time:

```bash
curl http://127.0.0.1:8010/api/calls/status
```

---

## 2. Import the workflow

`docs/n8n/saarthi_voice_workflow.json` contains four chains on one canvas:

| Webhook path | Who calls it | What it does |
|---|---|---|
| `saarthi-outbound-call` | Saarthi dashboard | creates a Twilio call, returns `{call_sid}` |
| `saarthi-voice` | Twilio (voice URL) | greets, offers the keypad language menu, opens `<Gather>` |
| `saarthi-voice-turn` | Twilio (`<Gather action>`) | forwards the transcript to Saarthi, plays the reply |
| `saarthi-call-status` | Twilio (status callback) | forwards the status to Saarthi |

Import: **n8n -> Workflows -> Import from file**.

Then, inside the workflow:

1. **Set the two placeholders.** Open the `Config` node (first node of each chain feeds from
   it) and set:
   * `saarthi_base` - your `SAARTHI_PUBLIC_URL`
   * `saarthi_token` - the same value as `SAARTHI_INBOUND_TOKEN`
   * `twilio_sid` - your Twilio Account SID (used only in the REST URL)
   * `twilio_from` - your Twilio voice number
2. **Attach the Twilio credential** (`twilioApi`) to the `Twilio: create call` node. n8n
   signs the request; the token never appears in the workflow JSON.
3. **Activate** the workflow, then copy the production URL of `saarthi-voice`.
4. In the **Twilio console**, set your number's *A call comes in* webhook to that URL
   (`HTTP POST`), and the *Call status changes* callback to the `saarthi-call-status` URL.

---

## 3. Test it in three steps

```bash
# 1. the contract is visible and the secret is set
curl -s http://127.0.0.1:8010/api/calls/status | python -m json.tool

# 2. one spoken turn, exactly as n8n sends it
curl -s -X POST http://127.0.0.1:8010/api/calls/turn \
  -H "Content-Type: application/json" \
  -H "X-Saarthi-Token: <SAARTHI_INBOUND_TOKEN>" \
  -d '{"call_sid":"CAtest1","transcript":"Should I irrigate my wheat today?","phone":"+919876543210","language":"en"}'

# 3. the keypad menu: 2 = Hindi
curl -s -X POST http://127.0.0.1:8010/api/calls/turn \
  -H "Content-Type: application/json" -H "X-Saarthi-Token: <SAARTHI_INBOUND_TOKEN>" \
  -d '{"call_sid":"CAtest1","digits":"2","phone":"+919876543210"}'
```

A turn responds with:

```json
{
  "call_id": "call-...", "language": "en",
  "reply": "Irrigation: irrigate now. Soil moisture 13.4% against a field capacity ...",
  "audio_url": "https://<saarthi>/api/speech/audio/<hash>.wav",
  "intent": "irrigation", "agents": [1, 4, 5, 2, 7, 15],
  "follow_up": ["..."], "end_call": false, "next_action": "gather_speech"
}
```

`audio_url` is 8 kHz mono WAV, which Twilio `<Play>` accepts directly. If Azure Speech is
not configured, `audio_url` is `null` and `speech_note` explains why - the workflow then
falls back to Twilio `<Say>`, which is why both branches exist in the TwiML node.

Without the token the endpoint returns **401**. That is intentional: an open voice webhook
would let anyone spend your Twilio balance and read farmers' advisories.

---

## 4. Dashboard-side behaviour

* **Voice Calls -> Call Me Back** posts to `/api/calls/outbound`, which posts to your n8n
  webhook. If `N8N_BASE_URL` is empty, the call is stored as `mode: simulated` with the note
  "n8n telephony is not configured, so nothing was dialled" - and the call log shows it as
  simulated. Nothing is ever dialled silently.
* **Try a Call Turn** runs the whole pipeline locally (agents, RAG, Azure TTS) and shows what
  the farmer would hear. Useful on stage when you cannot rely on the venue's mobile network.
* The call log keeps every turn's question, reply, language and audio URL.

---

## 5. If you prefer to keep your existing `Agri_Voice_Agent`

Your current n8n voice workflow already holds the `twilioApi` credential and a Mistral
chain. To point it at Saarthi instead, replace its "ask the model" step with a single
HTTP Request node:

* **Method** POST, **URL** `{{saarthi_base}}/api/calls/turn`
* **Header** `X-Saarthi-Token: {{saarthi_token}}`
* **Body (JSON)**
  ```json
  {
    "call_sid": "={{ $json.body.CallSid }}",
    "transcript": "={{ $json.body.SpeechResult || '' }}",
    "digits": "={{ $json.body.Digits || '' }}",
    "phone": "={{ $json.body.From }}"
  }
  ```

Everything else in that workflow (Twilio nodes, MongoDB logging, the OTP sign-in chain) can
stay exactly as it is. Saarthi returns the reply and the audio URL; the workflow plays it.


## Force end call

Pressing **Force end call** stops a call. What that means depends on how far along it
is, and the dashboard says which of the three happened rather than claiming success
for all of them:

| Call state | What happens |
|---|---|
| Booked, not yet dialled | Genuinely cancelled. Nothing rings. |
| Connected | Saarthi answers the next turn with a goodbye and `end_call: true`, and Twilio hangs up. Usually a few seconds. |
| Ringing, not yet answered | There are no turns to carry the instruction, so it rings out. The dashboard says so. |

The middle row is the fallback, and it works with no extra setup. Saarthi cannot ask
Twilio to drop a call itself, because that needs the Twilio auth token and the token
lives only in n8n's encrypted credential store - which is where it should stay.

**For an immediate hangup**, add one more webhook to n8n and set
`N8N_CALL_HANGUP_PATH`. Saarthi POSTs:

```json
{ "call_id": "call-...", "call_sid": "CA...", "action": "hangup",
  "requested_at": "..." }
```

The workflow should call Twilio's **Update Call** with `Status=completed` on that
`call_sid` (HTTP Request node to
`https://api.twilio.com/2010-04-01/Accounts/{{AccountSid}}/Calls/{{call_sid}}.json`,
your existing Twilio credential, body `Status=completed`), then **respond** with:

```json
{ "hung_up": true }
```

That confirmation matters. If the webhook answers anything else - or does not answer -
Saarthi does **not** record the call as ended; it falls back to the turn method above.
A hangup we only think happened is worse than one we know did not, because the farmer
is told the call was cut while it keeps talking.

---

## Letting Saarthi hear which language the caller is speaking

A Twilio `<Gather input="speech">` is given ONE locale for the whole call. A
caller who switches to Punjabi is still transcribed by whichever engine the
Gather was opened with, so the transcript comes back in the wrong script - and
until now the reply came back in the wrong language with it.

`POST /api/voice/turn` now identifies the language **from the audio** when the
audio is sent with the turn. Azure is given all thirteen candidate locales and
returns the one it heard, with a confidence. Measured end to end against the
live service:

```
spoke Punjabi -> language=pa  source=azure-lid  conf 0.84  listen_locale pa-IN
spoke Tamil   -> language=ta  source=azure-lid  conf 0.91  listen_locale ta-IN
spoke English -> language=en  source=azure-lid  conf 0.94  listen_locale en-IN
```

### It is built and live

`Saarthi_Conversation_Agent` (id `SaarthiConv00001`) carries it. Three nodes
were added between the turn webhook and Saarthi:

```
Twilio: farmer spoke -> Config turn -> Was it recorded?
                                         | yes -> Twilio: fetch the recording -\
                                         | no  ----------------------------------> Build the turn -> Saarthi: conversational turn
```

* **Was it recorded?** - an If on `RecordingUrl`, so a turn with only a
  transcript still goes straight through.
* **Twilio: fetch the recording** - a GET of `{{RecordingUrl}}.wav` with the
  Twilio credential the workflow already had. Set to continue on error and to
  retry three times: a recording Twilio has not finished writing is a wait, not
  a failed call.
* **Build the turn** - assembles the body, with the audio as `audio_b64`.

### Record or Gather

Both Config nodes have an `input_mode` field:

| `input_mode` | what happens | the trade |
|---|---|---|
| `record` (set) | the caller's audio goes to Azure, which returns the words AND the language | right language every time; about a second more per turn |
| `gather` | Twilio's own recognition, as before | faster; wrong whenever the caller is not speaking the locale the Gather was opened with |

On the `record` path Saarthi gets the transcript from **Azure**, in the correct
script. That is the half the old setup could not do: `<Gather>` hands back text
only, in whatever locale it was opened with, so a caller who switched to
Punjabi arrived as mangled Devanagari with no audio to check it against.

`<Record>` is followed by a `<Redirect>` on purpose. Twilio does **not** post to
the action when nothing at all was recorded - it falls through to the next verb
- so without the Redirect the call would go silent the first time somebody
hesitated. With it, the turn comes back with no transcript and no audio, and
Saarthi nudges them.

### Measured, end to end, through the live webhook

A Punjabi recording and a Tamil one, posted to the production webhook as Twilio
would:

```
caller switched hi -> pa (0.77)    heard  ਮੈਨੂੰ ਅੱਜ ਕਣਕ ਦਾ ਭਾਅ ਦੱਸੋ ਜੀ।
                                   said   ਜੀ, ਮੇਰੇ ਕੋਲ ਅੱਜ ਦਾ ਕਣਕ ਦਾ ਭਾਅ ਨਹੀਂ ਹੈ ਜੀ।
caller switched hi -> ta (0.91)    heard  இன்று கோதுமை விலை என்ன சொல்லுங்கள்?
                                   said   இன்றைய modal price எனக்குக் கிடைக்கவில்லை.
```

Neither request carried a `SpeechResult` at all: both transcripts came from
Azure, from the audio.

### The trap that cost an hour

`item.binary.recording.data` is the base64 **only when n8n keeps binaries in
memory**. This instance uses the filesystem store, where that field is an id -
and sending the id produced a cheerful HTTP 422 from Azure, no identification,
and a call that quietly stayed in one language. The Build node reads the bytes
with `await this.helpers.getBinaryDataBuffer(0, 'recording')`, which is right
either way.

### Re-importing it

```bash
docker cp workflow.json n8n-kmar-n8n-1:/home/node/w.json
docker exec n8n-kmar-n8n-1 n8n import:workflow --input=/home/node/w.json
docker exec n8n-kmar-n8n-1 n8n update:workflow --id=SaarthiConv00001 --active=true
cd /opt/stacks/n8n-kmar && docker compose restart      # activation needs it
```

**An import DEACTIVATES the workflow.** It says so in one line and is easy to
miss; the calling bot is dead until the two commands after it have run. The
version from before this change is kept at
`/opt/stacks/n8n-kmar/backups/Saarthi_Conversation_Agent.before-audio.*.json`.

### What it will not do

* It never changes the opening greeting. Identification starts with the
  caller's first real answer.
* A clip under ~1.2 seconds never changes the language. "Haan" is agreement,
  not a switch.
* Below 0.55 confidence it keeps the language the conversation was already in
  and reports `language_source: "kept"` with `detected_language: ""` - it does
  not guess, and it never claims a detection it did not make.
* With no audio at all, nothing is claimed: the fields are absent and the turn
  falls back to reading the transcript's script, which settles eleven of the
  thirteen on its own.

### Why a turn took ten seconds, and what it costs now

The server answers in about 2.5 s. A farmer on a real call was waiting ten.
The difference was all in the telephony leg, and most of it was one attribute:

```
3.0 s     <Record timeout="3">     dead air AFTER the caller stopped talking
0.5-1 s   Twilio finalises the recording and posts to n8n
0.3-2.4 s n8n fetches the recording (3 tries, 700 ms apart on failure)
2.5 s     the Saarthi turn
0.3-0.8 s Twilio fetches the clip to play
-------
6.6 - 9.7 s
```

Three seconds of silence to end a turn is the single largest piece, paid on
every turn, and the caller spends it wondering whether the line has dropped.
It is `record_silence` on both Config nodes now, defaulting to **1** - Twilio's
minimum. Raise it for somebody who thinks out loud mid-sentence; it needs no
deploy. `record_max_seconds` (default 20) is the other half of that pair.

The recording fetch retries **three times, 350 ms apart, with a 6 s request
timeout** instead of three times 700 ms apart with 20 s. Cutting it to two
tries saved another half-second and was reverted: losing the recording loses
the language identification with it, which is the part that works, and a
recording Twilio has not finished writing in 250 ms is common. 0.7 s of
waiting against the original 2.4 s is the trade that keeps both.

What is left is roughly `1.0 + 0.75 + 2.0 + 0.5`, so about **four seconds**.
The two ends of that - Twilio ending the recording, and Twilio fetching the
clip back from a server in Europe - are not ours to remove.
