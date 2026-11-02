# Saarthi 2.0 — Mobile App Build Specification

**Use this document as the build prompt.** It describes every screen, every feature and
every rule of the Saarthi 2.0 Android/iOS app. The app is a **client of an existing,
live backend** — it does not reimplement the farm intelligence, it consumes it.

- **Live API base URL:** `https://saarthi2.0.visioncoders.in`
- **Backend:** FastAPI + SQLite, 107 REST endpoints, 15 agricultural agents
- **Project:** InduX 5.0 Hackathon, Problem Statement 16, Team ID InduX5-26VU-404
- **Existing web dashboard:** `https://saarthi2.0.visioncoders.in/app/` (feature parity target)

---

## 0. One-paragraph brief

Saarthi is an AI farming companion for Indian smallholder farmers. It reads a real soil
sensor over Bluetooth, combines that with live weather, live mandi prices and a
13-language conversational AI, and runs a 15-agent reasoning chain to produce advice the
farmer can act on. It works for farmers who cannot read, through a phone call in their
own language, and for farmers with a smartphone, through this app. **The single
non-negotiable principle: Saarthi never invents a number.** Every figure on screen
carries its provenance, and when data is unavailable the app says so instead of
guessing.

---

## 1. Platform and stack

| Decision | Choice | Why |
|---|---|---|
| Framework | **React Native (Expo bare) or Flutter** | Needs native BLE, camera, background tasks, telephony intents. Expo Go alone is not enough — BLE requires a dev build. |
| Minimum Android | **API 24 (Android 7.0)** | Target devices are ₹6,000–12,000 phones. |
| iOS | 14+ | Secondary; Android is the primary target. |
| BLE | `react-native-ble-plx` / `flutter_blue_plus` | Replaces the web's Web Bluetooth. |
| Local DB | SQLite (`expo-sqlite` / `drift`) | Offline queue and cache. |
| State | React Query / Riverpod | Server-state caching with stale-while-revalidate. |
| Push | **FCM** | Replaces the web's localStorage notification bell. |
| Maps | `react-native-maps` with **OpenStreetMap tiles** | OSM, not Google/Carto — matches the web app and avoids billing. |

**Design for the worst device on the worst network.** A 2G connection in a field is the
design case, not an edge case.

---

## 2. Authentication and accounts

### 2.1 The account model

- One account = one **phone number** + one **email**. The phone number is the identity
  that links the dashboard, the app and the phone-call channel: a fact told to the
  voice bot appears in the app, and vice versa.
- Roles: **`user`** and **`admin`**. Admin unlocks the Admin screen (§6.17).
- Status: `pending` (registered, not verified), `active`, `locked`, `disabled`.
- Passwords are hashed server-side with **scrypt** + per-user salt. Session tokens are
  **SHA-256**. The app never sees or stores a raw password beyond the input field.
- Failed sign-ins are counted server-side; too many → `locked_until`.

### 2.2 Screens to build

**S1 — Splash / boot**
Check for a stored session token → `GET /api/auth/me`. If `signed_in: true`, go to
Overview. Otherwise go to Welcome. Show nothing but the logo for at most 2 seconds; on
a timeout, go to Welcome rather than hanging.

**S2 — Welcome**
Language picker first (13 languages, each in its own script — see §17), then
`Sign in` / `Create account`. The language choice must be possible **before** sign-in,
because a farmer who cannot read English cannot find the sign-in button otherwise.

**S3 — Create account**
Fields: full name, phone (10 digits, India), email, password, confirm password,
preferred language.
- `GET /api/auth/captcha` → show the challenge, send the answer with registration.
- `POST /api/auth/register` → server sends a 6-digit OTP to **both email and SMS**.
- Go to S4.

**S4 — Verify code (sign-up)**
Six-digit OTP box with auto-advance and paste support.
- `POST /api/auth/verify` `{phone, code}` → on success the account is `active` **and
  signed in** (session returned). Go to S6 onboarding.
- `POST /api/auth/resend` with a 30-second cooldown and a visible countdown.
- Wrong code: show attempts remaining, never "invalid credentials" vagueness.

**S5 — Sign in (two-step)**
1. `POST /api/auth/login` `{phone_or_email, password}` → **this does not sign you in.**
   It validates the password and sends an OTP.
2. `POST /api/auth/login-verify` `{phone, code}` → returns the session token.

Store the token in **Keychain / EncryptedSharedPreferences**, never in plain
AsyncStorage. Send it as `Authorization: Bearer <token>`.

**S5a — Forgot password**
- `POST /api/auth/forgot` `{phone_or_email}` — **answers identically whether or not the
  account exists.** Do not let the UI leak the difference either: the same success
  screen, always.
- `POST /api/auth/reset` `{phone, code, new_password}`.

**S5b — Change password** (inside Profile)
`POST /api/auth/change-password` `{current_password, new_password}`.

**S6 — Onboarding (first run only)**
A short wizard, skippable, that collects what the agents need:
1. Village, tehsil, district, state, pincode
2. Total land (acres), soil type, irrigation method, farming since
3. **Add your first field** — name, crop, sowing date, area, and location (drop a pin
   on the map or use GPS)
4. Permissions explainer (§22)

Everything here is also editable later in Profile, and **every step is skippable** — a
farmer who skips still gets a working app, just with more "I need to know X" answers
from the agents.

**S7 — Sign out**
`POST /api/auth/logout`, clear the token and all cached farm data from the device.

### 2.3 Session rules

- On any `401`, clear the session and return to S5 with "Your session expired, please
  sign in again." Never silently fail.
- Sessions survive app restarts. Do not force a re-login on every cold start.
- Biometric unlock (fingerprint) is an **optional convenience** on top of a stored
  session, not a replacement for it.

---

## 3. Navigation map

Bottom tab bar, five tabs, because more than five is unusable on a small screen:

| Tab | Contains |
|---|---|
| **Home** | Overview, pipeline chain, run advisory |
| **My Farm** | Soil Health, Weather, Crop Advisor, Pest & Disease, My Fields, Sensor |
| **Money** | Market Prices, Farm Finance, Govt Schemes, Farm Support |
| **Ask** | AI Assistant (chat), Voice Calls, Call-backs |
| **Me** | Profile, Reports, AI Agents, Settings, Admin (admins only) |

A persistent **microphone FAB** floats above the tab bar on every screen — one tap to
ask Saarthi anything by voice. This is the primary input method for low-literacy users
and must never be more than one tap away.

---

## 4. The non-negotiable rules

These are what make Saarthi trustworthy. Breaking any of them is a bug, not a
preference.

1. **Never invent a number.** If a service is unavailable, show *why* — "Could not
   reach the market service" — not a stale or plausible figure.
2. **Every number carries provenance.** Show it as a small badge:
   `real_sensor` · `device_simulated` · `farmer_reported` · `simulated` · `lab` ·
   `live_api` · `knowledge_base` · `estimated` · `unavailable`.
   `device_simulated` especially: the packet genuinely arrived over BLE, but the device
   generated the number itself. That is neither "measured" nor "simulated by us", and
   collapsing it into either is how a demo figure gets quoted as a measurement.
3. **Four agent states, shown differently:** `ok`, `needs_input` (the farmer can fix
   it — ask them), `unavailable` (*we* have a gap — never ask the farmer for something
   no typing could fix), `error`.
4. **An English label is a minor annoyance; a blank one is a broken app.** Failed
   translation falls back to English, never to empty.
5. **Never ask for data the farmer already gave.** If the field record has the crop,
   do not ask for the crop.
6. **Offline is a state, not an error.** Show cached data with its age, clearly marked.

---

## 5. Offline behaviour (mobile-specific, critical)

The web app assumes a connection. **The mobile app must not.**

| Feature | Offline behaviour |
|---|---|
| Last advisory | Cached in full, readable, stamped "from 2 hours ago" |
| Soil readings | Cached; **BLE still works offline** — the sensor is local |
| Ledger entries | **Queued locally and synced when back online.** A farmer entering a cost in a field with no signal must not lose it. |
| Weather / market | Cached with age shown; refuses to show anything older than 24h without saying so |
| Voice chat | Requires a connection — say so plainly, offer "book a call-back" instead |
| Crop profiles, schemes | **Bundled in the app**, fully offline — they change rarely |
| Pipeline run | Requires a connection |

Show a persistent, non-blocking banner when offline. Never a modal — a farmer in a
field cannot dismiss a dialog to see their own data.

---

## 6. Screen-by-screen specification

### 6.1 Overview (Home)

`GET /api/dashboard/overview?parcel_id=<id>`

- **Greeting** with the farmer's real name and time of day.
- **Field selector** at the top — every card below reflects the *selected* field. Each
  field has its own crop, its own coordinates and therefore its own weather.
- **Four summary cards:** Soil health index, Weather now, Crop condition, Market price.
- **The 15-agent chain diagram** (§7) — the signature visual.
- **Recent activity** list.
- **Run Advisory** button (one, not two).
- **Fetch data** button for the sensor.

### 6.2 Pipeline Results

`GET /api/pipeline/last`, `GET /api/pipeline/progress`, `POST /api/pipeline/run`,
`POST /api/pipeline/stop`

- The integrated advisory from Agent 15, in full, **in the farmer's language**.
- Per-agent accordion: headline, findings, actions, assumptions, evidence, provenance.
- Agents that need input show an inline form — **collected before the chain runs**, not
  after it fails.
- **Force-stop** control. A browser aborting its request frees the page but leaves the
  work running; the app must call `POST /api/pipeline/stop` explicitly.
- Export to PDF.

### 6.3 Soil Health

`GET /api/sensors/latest`, `GET /api/sensors/history`, `GET /api/soil/background`

Seven parameters, each with its value, unit, agronomic band and provenance badge:

| Parameter | Unit | Plausible range | Typical agronomic band |
|---|---|---|---|
| Nitrogen (N) | mg/kg | 0–2000 | 20–60 |
| Phosphorus (P) | mg/kg | 0–2000 | 10–25 |
| Potassium (K) | mg/kg | 0–2000 | 120–280 |
| Soil temperature | °C | −10–60 | 15–35 |
| Soil moisture | % | 0–100 | 25–60 |
| EC | dS/m | 0–10 | 0.2–1.6 |
| pH | pH | 3–10 | 6.0–7.5 |

A reading older than **6 hours** is marked stale. Show a trend chart per parameter and
a "where this came from" line (SoilGrids background vs. the farmer's own probe).

### 6.4 Weather

`GET /api/weather?parcel_id=<id>`

Source: **Open-Meteo**, for the *selected field's* coordinates — not a district
centroid, and not the phone's current location unless the farmer asks for that
explicitly. Current conditions plus a 7-day forecast: max/min temp, rain mm, rain
probability, wind, ET₀. Derived advisories: spraying window, irrigation need, frost
risk, heat stress.

### 6.5 Crop Advisor

`GET /api/crops?season=rabi|kharif|zaid`

Cards in a grid, scored 0–100 by Agent 3 from the farmer's soil pH, EC, the season and
water availability. **The score compares crops with each other — it is not a yield
prediction, and the screen must say so.**

Current catalogue (18 profiles):
- **Rabi (12):** wheat, mustard, chickpea, onion, lentil, field pea, garlic,
  coriander, fenugreek, maize, barley, potato
- **Kharif (4):** rice, tomato, maize, cotton
- **Zaid (4):** tomato, watermelon, muskmelon, cucumber

Each card: duration (days), water need (mm), sowing window, and on tap the full profile
— pH band, EC tolerance, seed rate, row spacing, sowing depth, and the **stage timeline
with Kc values and a note per stage**.

> Layout rule: the grid is 4 columns (2 on a phone). Keep each season's crop count a
> multiple of 4 or the last row has holes.

### 6.6 Pest & Disease

`POST /api/vision/diagnose` (multipart image), `GET /api/vision/status`

- **Camera-first.** Open the camera, not a file picker.
- On-device guidance before the shot: fill the frame with one leaf, daylight, steady.
- Model: EfficientNetB0, **32 classes**, trained on 39.5k images, exported to ONNX and
  run server-side.
- **Confidence threshold 0.90.** Below it the app refuses to name a disease and says
  what to do instead — this is deliberate and must not be "helpfully" softened.
- Always pair the diagnosis with: *confirm with your local agriculture officer before
  spraying.*
- Keep a local history of past diagnoses with their photos.

### 6.7 Govt Schemes

`GET /api/schemes`

7 schemes, ranked for the farmer's own farm, each with category, subsidy, eligibility,
documents needed, how to apply, and the official portal link:

PM-KISAN · Kisan Credit Card (KCC) · PMKSY micro-irrigation · PMFBY crop insurance ·
Soil Health Card & fertiliser subsidy · SMAM mechanisation · PM-KUSUM solar pump

Filter by category. **Never collect credentials or impersonate a government portal** —
link out to the official site in the system browser.

### 6.8 Farm Finance (Ledger)

`GET /api/ledger`, `POST /api/ledger`, `DELETE /api/ledger/{entry_id}`,
`GET /api/ledger/summary`

- Record **expense** or **income** per field, per season, per category:
  seed, fertiliser, pesticide, irrigation, labour, machinery, transport, storage, other.
- Season is computed **on write and stored**, so changing the season logic later cannot
  silently re-file old entries.
- Totals: Spent, Earned, Ahead/Behind by.
- **Voice entry:** "I spent 4200 on urea", "majdoor ko 1800 diye", "sold the wheat for
  12500". Deliberately narrow — a digit run plus a category word. Spelled-out amounts
  ("four thousand") are refused rather than guessed, because filing a wrong amount
  silently is worse than asking again.
- An empty record reports zeros and says the record is empty. **There is deliberately
  no district cost-of-cultivation fallback** — that number would be read back as the
  farmer's own.
- **Offline-queued** (§5).

### 6.9 Market Prices

`GET /api/market?commodity=&state=&district=&parcel_id=`

Live **data.gov.in Agmarknet**. Defaults to the selected field's district, then widens
district → state → national, **reporting which scope it used** — "the price in your
district" and "the price somewhere in India" are very different numbers to sell on.

Shows median modal price, highest with its market name, record count, arrival date.
Nearest markets are ordered by **district match then price — not distance**, because
Agmarknet records carry no coordinates and a computed distance would be invented.

Always show: *prices are as reported by the listed markets on the stated arrival date,
in ₹/quintal. Past prices do not predict future prices.*

### 6.10 Farm Support

`GET /api/support` — 5 national helplines, 5 official portals, each tappable
(`tel:` / browser). Says plainly that the block agriculture officer is the local
authority.

### 6.11 AI Assistant (chat)

`POST /api/chat`, `GET /api/chat/history`, `GET /api/chat/quick-prompts`,
`POST /api/chat/session/{id}/language`

- RAG-backed over the farm knowledge base, plus the farmer's live farm context.
- **20 intents** routed rule-first: soil_health, nutrient, irrigation, crop_selection,
  crop_stage, weather, pest_disease, weed, seed_sowing, crop_protection, market,
  economics, scheme, finance, support, ledger, **task**, residue, sensor, general.
- Typed or spoken; replies read aloud via TTS.
- Shows which agents were consulted and the evidence behind the answer.
- Quick-prompt chips for common questions.
- Per-farmer memory keyed on the last 10 digits of the phone number.

### 6.12 Voice Calls

`POST /api/calls/outbound`, `POST /api/calls/cancel`, `GET /api/calls`,
`GET /api/calls/status`, `GET /api/calls/ivr/languages`

Saarthi calls the farmer on an ordinary phone — **this is the feature that reaches
farmers with no smartphone at all**, and the app is the control panel for it.

- Request a call-back; the delay is set in Profile.
- **One call at a time** — the guard is server-side and the app must show *why* a
  second request was refused.
- Call log with honest per-status labels. A call is reported as `requested` **only once
  Twilio has accepted it**, never merely because n8n answered 200.
- **Force end call** — and this is nuanced. Saarthi cannot hang up Twilio directly (the
  auth token lives only in n8n's encrypted credential store). Three outcomes, reported
  as three:
  - *Booked, not dialled* → genuinely cancelled, nothing rings
  - *Connected* → the next turn of the call gets `end_call: true` and Twilio hangs up,
    a few seconds. Status shows **"Ending — hangs up at the next turn"**, not
    "Cancelled", so it never claims the call is over while the farmer can still hear it
  - *Ringing, unanswered* → no turns exist to carry the instruction, so it rings out,
    and the app says exactly that
- IVR keypad language menu (DTMF 1–13).

### 6.13 Call-back Tasks ⭐

`GET /api/tasks`, `POST /api/tasks`, `DELETE /api/tasks/{task_id}`

Book Saarthi to **ring you later**. Plain language in one box — the same parser the
phone line uses, so any phrase that works there works here.

**Three kinds:**

| Kind | Example | Behaviour |
|---|---|---|
| `reminder` | "remind me in 1 hour to irrigate field 1" | Says your own words back |
| `question` | "tell me the wheat price after 30 minutes" | **Looks it up when the phone rings, not when you book it** |
| `watch` | "call me when wheat goes above 2400" / "call me if rain is coming" | Re-checked every 20s until it happens or expires after 7 days |

**The fire-time resolution is the whole point.** A price asked for "in 30 minutes" is
fetched in 30 minutes. Resolving at booking time would hand the farmer a half-hour-old
number while sounding completely certain.

Parses English, Hinglish and Devanagari: "after 30 minutes", "30 minute baad",
"१ घंटे बाद", "kal subah", "shaam 6 baje", "half an hour".

Rules the app must surface:
- The confirmation **names the time back** — the farmer's only check that the parser
  heard the right number.
- An unparseable request **books nothing** and asks "when?" — never a half-booking.
- A failed lookup at fire time says so, with **no digits in the sentence**.
- A watch that never triggers **calls to say it has stopped** — an unkept promise the
  farmer never hears about is worse than a refusal.
- Quiet hours (21:00–06:30 IST) apply **only to moments nobody chose** — a watch
  triggering overnight. A time the farmer explicitly asked for is honoured at any hour,
  because night irrigation is routine when pump electricity is supplied at night.

### 6.14 My Fields

`GET /api/profile/fields`, `POST /api/profile/fields`,
`DELETE /api/profile/fields/{field_id}`

Per field: name, crop, sowing date, area, soil type, irrigation method, and a mapped
location. Map features: zoom, fullscreen, **current location**, and **registered land
locations**. Tiles from **OpenStreetMap**. Coordinates stored at **6 decimal places** —
3 dp is ~100 m, which is a different field.

### 6.15 Sensor

`POST /api/sensors/ble`, `POST /api/sensors/manual`, `GET /api/sensors/latest`,
`GET /api/sensors/register-map`, `GET /api/sensors/history`

Hardware: **RS485 7-in-1 soil probe → Arduino Uno → HM-10 BLE module → phone.**

- BLE service `0000ffe0-0000-1000-8000-00805f9b34fb`,
  characteristic `0000ffe1-0000-1000-8000-00805f9b34fb`.
- **Native BLE on mobile is a major upgrade over the web's Web Bluetooth** — background
  scanning, auto-reconnect, no browser permission dance.
- **Fetch-only updates.** Values update when the farmer presses Fetch, never silently
  in the background. A farmer looking at a number must be able to trust it is the one
  they asked for.
- **Label-only parsing — no positional fallback, ever.** Positional parsing once filed
  phosphorus as nitrogen. If a label is missing, that field is simply absent.
- The parser strips trailing unit tokens (`EC_US_CM` → field `ec`, unit `us/cm`) and
  uses two lookaheads so `LAT: 27.1 LON: 78.0` cannot read "LO" as a unit and file
  longitude as nitrogen.
- Manual entry as a fallback, marked `farmer_reported`.
- Shows the sensor path as a single horizontal line above the cards.

### 6.16 Reports

`GET /api/reports`, `GET /api/reports/{report_id}?format=json|pdf|xlsx`

Four reports — **Soil health**, **Advisory history**, **Call log**, **Sensor history** —
each downloadable as **PDF or Excel**, then shareable through the OS share sheet
(WhatsApp is how these actually travel).

### 6.17 AI Agents

`GET /api/agents`, `GET /api/agents/routing`, `POST /api/agents/run`

The 15 agents with their dependencies and what each one needs (§7). Run a single agent
on demand. Architecture diagram.

### 6.18 Profile & Settings

`GET /api/profile`, `POST /api/profile`, `DELETE /api/profile/memory`,
`DELETE /api/history`

Personal details, farm details, language, **dark mode**, call-back delay, notification
preferences, change password, sign out.

Data controls: clear advisory/call/session history, clear remembered facts.
`DELETE /api/history?what=all` clears advisories, calls and sessions but **not
readings** — those require `what=readings` by name, because a farmer clearing a
question log would not expect their soil history to go with it.

> **Known bug to avoid porting:** the web app showed stale pre-filled name/village until
> the profile icon was tapped. Load the real profile **before** first paint.

### 6.19 Admin (role = admin only)

`GET /api/admin/users`, `POST /api/admin/users/{id}/role`,
`POST /api/admin/users/{id}/status`, `DELETE /api/admin/users/{id}`,
`GET /api/admin/otp-status`, `GET /api/keys/status`

User list, role and status changes, delete, OTP delivery health, service-key status.
**Hide the tab entirely for non-admins** — do not merely disable it.

---

## 7. The 15-agent pipeline

Five dependency waves. Conversational AI, the LLM, RAG and the orchestrator are
**shared capabilities, not agents** — do not draw them as nodes.

| # | Agent | Depends on |
|---|---|---|
| 1 | Soil Sensor Acquisition and Data Quality | — |
| 2 | Soil Health and Interpretation | 1 |
| 3 | Crop Suitability and Selection | 2 |
| 4 | Crop Growth Stage and Agronomy | — |
| 5 | Weather and Climate Risk | — |
| 6 | Nutrient Management and Fertiliser | 1, 2, 4 |
| 7 | Irrigation and Water Management | 1, 2, 4, 5 |
| 8 | Pest and Disease Risk | 4, 5 |
| 9 | Weed Management | 4 |
| 10 | Seed, Sowing and Crop Establishment | 3, 5 |
| 11 | Crop Protection and Safe Application | 8, 9, 5 |
| 12 | Agricultural Market Intelligence | — |
| 13 | Farm Economics and Profitability | 12 |
| 14 | Sustainable Farming and Crop Residue | — |
| 15 | Integrated Advisory and Recommendation Validation | 1–14 |

**Chain animation rules:**
- Two rows, both left-to-right, centred; agent 9 sits under agent 1.
- START and FINISH are **red until the run actually reaches them** — they used to be
  green on load, which said the run had finished before it began.
- Nodes turn green in order, in a smooth slow transition, **synced to real progress**
  from `GET /api/pipeline/progress` — never a fake animation.
- On error, **freeze the chain where it actually got to** and raise a notification that
  the chain aborted. Do not leave it half-lit with no explanation.

---

## 8. Notifications (push)

Replace the web's localStorage bell with **FCM**, plus an in-app centre. Triggers:

- Mandi price moved **≥5%** since the last visit
- **≥10 mm** rain appearing in the 3-day forecast
- A new government scheme in the catalogue
- A pipeline run aborted or stopped
- A booked call-back is about to fire, fired, or failed
- A watch triggered or expired
- Services not configured (admin only)

Scheme alerts use a digest of scheme **ids + titles, not contents** — correcting a
subsidy percentage must not look like a new scheme. There is no government feed, so the
wording is honestly *"the list on this dashboard has gained something"*, never *"a
scheme was launched"*.

---

## 9. Language and translation

13 languages: **English, Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada,
Malayalam, Punjabi, Odia, Assamese, Urdu.**

`GET /api/i18n/languages`, `POST /api/i18n/translate`

- The interface is translated **by the LLM with a server-side cache** — not a
  hand-written catalogue. Each string is translated once per language, ever, and the
  cache is shared by all users.
- Send strings in chunks of ~40, **max 6 concurrent requests** — firing them all at
  once trips the model API's rate limit, and a rate-limited chunk silently falls back
  to English.
- Anything over 400 characters goes in a **request of its own** with a token budget
  scaled by character count (Devanagari costs several tokens per character).
- **Cache translations on the device** so a second launch is instant and offline.
- English is the source and is never stored. Switching back to English is an **exact
  restore**, not a re-translation.
- **Never translate:** numbers, units (kg, mm, dS/m, pH, quintal, acre), scheme names
  and abbreviations (PM-KISAN, KCC, PMFBY), place names, the product name *Saarthi*,
  timestamps, device IDs, and **the language picker's own options** — translating that
  menu into the language just picked leaves no readable way back.
- RTL layout for Urdu.
- A failed translation shows **English, never blank**.

---

## 10. Speech

`POST /api/speech/tts`, `POST /api/speech/stt`, `GET /api/speech/status`,
`POST /api/voice/transcribe`, `POST /api/voice/turn`

- **Sarvam AI** for Indian-language TTS (`bulbul`) and STT (`saarika`).
- Azure Speech exists in the codebase but is **disabled** — leave it off.
- Every AI answer has a **play button**. Autoplay only if the farmer opted in.
- Hold-to-talk on the mic FAB, with a live waveform and a visible transcript so the
  farmer can see what was heard before it is sent.

---

## 11. Full API reference (107 endpoints)

**Auth** — `GET /api/auth/me` · `GET /api/auth/captcha` · `POST /api/auth/register` ·
`POST /api/auth/verify` · `POST /api/auth/resend` · `POST /api/auth/login` ·
`POST /api/auth/login-verify` · `POST /api/auth/forgot` · `POST /api/auth/reset` ·
`POST /api/auth/change-password` · `POST /api/auth/logout`

**Profile & fields** — `GET|POST /api/profile` · `GET|POST /api/profile/fields` ·
`DELETE /api/profile/fields/{id}` · `DELETE /api/profile/memory` · `GET /api/farm` ·
`GET /api/farmers` · `GET|POST /api/farmers/{phone}` ·
`DELETE /api/farmers/{phone}/facts/{key}`

**Dashboard & insights** — `GET /api/dashboard/overview` · `GET /api/weather` ·
`GET /api/market` · `GET /api/crops` · `GET /api/soil/background` · `GET /api/finance` ·
`GET /api/schemes` · `GET /api/support` · `GET /api/reports` ·
`GET /api/reports/{id}` · `DELETE /api/history`

**Sensors** — `POST /api/sensors/ble` · `POST /api/sensors/manual` ·
`POST /api/sensors/ingest` · `GET /api/sensors/latest` · `GET /api/sensors/history` ·
`GET /api/sensors/register-map`

**Pipeline & agents** — `POST /api/pipeline/run` · `POST /api/pipeline/stop` ·
`GET /api/pipeline/progress` · `GET /api/pipeline/last` · `GET /api/pipeline/status` ·
`GET /api/pipeline/inputs` · `GET /api/pipeline/agent-status` · `GET /api/agents` ·
`GET /api/agents/routing` · `POST /api/agents/run` · `POST /api/intent`

**Chat & RAG** — `POST /api/chat` · `GET /api/chat/history` ·
`GET /api/chat/quick-prompts` · `GET /api/chat/session/{id}` ·
`POST /api/chat/session/{id}/language` · `POST /api/rag/search` ·
`GET /api/rag/stats` · `POST /api/rag/reindex`

**Calls & voice** — `POST /api/calls/outbound` · `POST /api/calls/cancel` ·
`GET /api/calls` · `GET /api/calls/{id}` · `GET /api/calls/status` ·
`POST /api/calls/turn` · `POST /api/calls/status-callback` ·
`POST /api/calls/simulate` · `GET /api/calls/ivr/languages` · `GET /api/voice/status` ·
`POST /api/voice/turn` · `POST /api/voice/transcribe` · `POST /api/voice/end` ·
`GET /api/enrol/steps` · `POST /api/enrol/turn` · `POST /api/enrol/confirm`

**Tasks** — `GET /api/tasks` · `POST /api/tasks` · `DELETE /api/tasks/{id}`

**Ledger** — `GET /api/ledger` · `POST /api/ledger` · `GET /api/ledger/summary` ·
`DELETE /api/ledger/{id}`

**Vision** — `POST /api/vision/diagnose` · `GET /api/vision/status`

**Speech & i18n** — `POST /api/speech/tts` · `POST /api/speech/stt` ·
`POST /api/speech/token` · `GET /api/speech/audio/{name}` · `GET /api/speech/status` ·
`GET /api/i18n/languages` · `POST /api/i18n/translate` · `GET /api/languages`

**System & admin** — `GET /api/health` · `GET /api/keys/status` ·
`GET /api/admin/users` · `GET /api/admin/users/{id}` · `DELETE /api/admin/users/{id}` ·
`POST /api/admin/users/{id}/role` · `POST /api/admin/users/{id}/status` ·
`GET /api/admin/otp-status`

---

## 12. Error and empty states

Every screen needs four states: **loading**, **empty**, **error**, **offline**.

| Situation | Message |
|---|---|
| No sensor reading | "No sensor reading yet. Connect your device, or enter values by hand." |
| Service down | "Could not reach the weather service just now." — **and no number** |
| Agent needs input | The agent's own question, with an inline form |
| Agent unavailable | The agent's own explanation — **never a question the farmer cannot answer** |
| Call refused | The server's actual reason, not "something went wrong" |
| Offline | A persistent banner, cached data with its age |

Never show a raw HTTP code, a stack trace, or "Error 500" to a farmer.

---

## 13. Design system

- **Light and dark mode**, both fully supported. Known traps from the web build:
  hard-coded light gradients (`.weather-now`), `<select>` elements without an explicit
  `color`/`background`, and the greeting text disappearing in dark mode.
- **Large tap targets** — minimum 48×48dp. Users may be wearing gloves or have
  work-worn hands.
- **High contrast**, readable in direct sunlight.
- **Font scaling** up to 200% without breaking layout.
- Palette: agricultural greens with earth accents; one tint per crop on crop cards.
- Minimal text. The web version's biggest recurring complaint was too much text —
  **every label should earn its place.**
- Icons paired with text labels, never icons alone.

---

## 14. Permissions

Request each **in context, with a reason**, never all at launch:

| Permission | Needed for | Asked when |
|---|---|---|
| Bluetooth + Location | BLE soil sensor (Android requires location for BLE scan) | First "Connect device" |
| Camera | Leaf disease photos | First diagnosis |
| Location | Mapping a field, current weather | Adding a field |
| Notifications | Price/weather/call alerts | After first successful advisory |
| Microphone | Voice questions | First mic tap |
| Storage | Saving reports | First download |

Explain **why** before the system dialog. A denied permission must degrade gracefully,
never block the app.

---

## 15. Security

- Token in Keychain / EncryptedSharedPreferences — **never** plain storage.
- **Certificate pinning** on the API host.
- No API keys in the app bundle. All third-party services (Mistral, Sarvam,
  data.gov.in, Twilio) are called **server-side only**.
- Screenshot blocking on password screens.
- Clear all cached farm data on sign-out.
- **Never build a screen that imitates a government portal or collects credentials for
  one.** Link out to the official site instead.

---

## 16. Acceptance checklist

The build is done when a farmer can:

- [ ] Create an account, verify by OTP, and sign in with a two-step OTP flow
- [ ] Recover a forgotten password without the app leaking whether the account exists
- [ ] Use the whole app in any of 13 languages, switched in under a second, and switch back to exact English
- [ ] Add a field with a crop, a sowing date and a mapped location
- [ ] Connect the HM-10 over BLE and fetch real soil values, label-matched
- [ ] See those values reflected on **every** screen, with provenance badges
- [ ] Run the 15-agent chain and watch it animate in sync with real progress
- [ ] Stop a running chain
- [ ] Photograph a leaf and get a diagnosis, or an honest refusal below 0.90 confidence
- [ ] Ask a question by voice in Hindi and hear the answer read back
- [ ] Record "I spent 4200 on urea" by voice and see it in the ledger
- [ ] See live mandi prices for their own district, with the scope stated
- [ ] Book "tell me the wheat price in 30 minutes" and **receive a call with the price as it is then**
- [ ] Book "call me when wheat goes above 2400" and be called when it does
- [ ] End a live call and see honestly what happened
- [ ] Download a soil report as PDF and share it on WhatsApp
- [ ] Use the app with no signal and lose nothing they typed
- [ ] Never once see a number the system could not justify

---

## 17. Build order

1. Auth (S1–S7) + API client + token storage + i18n shell
2. Profile, fields, map
3. Overview + sensor BLE + Soil Health
4. Pipeline chain + Pipeline Results
5. Weather, Crop Advisor, Market
6. Chat + speech
7. Calls + call-back tasks
8. Ledger + offline queue
9. Vision, schemes, support, reports
10. Notifications, admin, polish

Ship 1–4 as the demo spine; it is the part that proves the system is real.
