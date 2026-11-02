# Saarthi 2.0 — Android Native Build Prompt (Kotlin + Jetpack Compose)

**Use this entire document as the build prompt.** It specifies every screen, every
feature, every API call and every behavioural rule of the Saarthi 2.0 Android app. Do
not summarise it, do not pick a subset, and do not substitute your own architecture for
the one described in §3.

The app is a **native Android client of an existing, live backend**. The farm
intelligence already exists and works — the app consumes it. Do not reimplement agents,
RAG, pricing, weather or speech on the device.

| Thing | Value |
|---|---|
| Live API base URL | `https://saarthi2.0.visioncoders.in` |
| Backend | FastAPI + SQLite, 107 REST endpoints, 15 agricultural agents |
| Web dashboard (feature-parity target) | `https://saarthi2.0.visioncoders.in/app/` |
| Project | InduX 5.0 Hackathon, Problem Statement 16, Team ID InduX5-26VU-404 |
| Target platform | **Android native only** — Kotlin + Jetpack Compose |

---

## 0. One-paragraph brief

Saarthi is an AI farming companion for Indian smallholder farmers. It reads a real
RS485 soil probe over Bluetooth, combines that with live weather, live mandi prices and
a 13-language conversational AI, and runs a 15-agent reasoning chain to produce advice
the farmer can act on. It serves farmers who cannot read, through a phone call in their
own language, and farmers with a smartphone, through this app.

**The single non-negotiable principle: Saarthi never invents a number.** Every figure on
screen carries its provenance, and when data is unavailable the app says so instead of
guessing. An app that shows a plausible soil reading it did not measure is a failed
build, however polished it looks.

---

## 1. Platform, stack and versions

| Decision | Choice | Why |
|---|---|---|
| Language | **Kotlin 2.0+**, JVM target 17 | Native Android. No Java sources. |
| UI | **Jetpack Compose** + Material 3 | Single declarative UI; no XML layouts except the splash theme. |
| minSdk | **24** (Android 7.0) | Target devices are ₹6,000–12,000 phones. |
| targetSdk / compileSdk | **35** (Android 15) | Play requirement; forces the modern permission model. |
| Architecture | **MVVM + Clean-ish layering**, unidirectional data flow | §3 |
| DI | **Hilt** | |
| Async | **Coroutines + Flow**; `StateFlow` for UI state | |
| Networking | **Retrofit 2 + OkHttp 4** + `kotlinx.serialization` | |
| Local DB | **Room** (+ Paging 3 for history lists) | Offline cache and the ledger write queue. |
| Preferences | **DataStore (Proto or Preferences)** | Language, theme, notification prefs. |
| Secrets | **EncryptedSharedPreferences** (`androidx.security:security-crypto`) | Session token only. Never DataStore, never plain prefs. |
| Background work | **WorkManager** | Ledger sync, translation prefetch, cache refresh. |
| BLE | **Nordic `no.nordicsemi.android:ble`** (or raw `BluetoothGatt`) | HM-10 serial link. §11 |
| Camera | **CameraX** | Leaf photos. §12 |
| Maps | **osmdroid** with OpenStreetMap tiles | OSM, not Google Maps and not Carto — matches the web app, no billing, no API key. |
| Images | **Coil** | |
| Charts | **Vico** (or hand-drawn Compose `Canvas`) | Sensor trends, price history. |
| Audio in | **AudioRecord** → 16 kHz mono PCM WAV | STT upload. §13 |
| Audio out | **Media3 / ExoPlayer** (or `MediaPlayer`) | TTS playback. |
| Push | **Firebase Cloud Messaging** | Replaces the web build's localStorage bell. §15 |
| Navigation | **Navigation-Compose**, type-safe routes | |
| Permissions | **Accompanist Permissions** or raw `ActivityResultContracts` | §19 |
| Build | Gradle KTS, version catalog (`libs.versions.toml`), R8 enabled for release | |
| Tests | JUnit5 + Turbine + MockWebServer; Compose UI tests; Robolectric for parsers | §22 |

**Design for the worst device on the worst network.** A 2 G connection in a field is the
design target, not a wifi desk. Cold start under 2 s on a 2 GB-RAM device. APK under
25 MB. No frame drops on the agent chain animation at 60 fps on a mid-range phone.

---

## 2. What already exists (do not rebuild)

The backend owns all of this. The app must call it, not duplicate it:

- The 15 agricultural agents and the orchestrator that sequences them
- RAG retrieval over the farm knowledge base
- Mistral LLM calls, Sarvam STT/TTS, interface translation with a server-side cache
- Live Open-Meteo weather, live data.gov.in Agmarknet prices, SoilGrids background soil
- Account storage, scrypt password hashing, OTP issue/verify, session tokens
- Outbound phone calls via n8n → Twilio, the IVR language menu, call-back task firing
- Leaf disease ONNX inference (EfficientNetB0, 32 classes, threshold 0.90)
- PDF and Excel report generation

**No third-party API key ever ships in the APK.** Mistral, Sarvam, data.gov.in and
Twilio are called server-side only. The app holds exactly one secret: the user's own
session token.

---

## 3. Module and package structure

Single Gradle module is acceptable for a hackathon build, but keep these package
boundaries strictly — the reviewer will look for them:

```
com.visioncoders.saarthi
├── SaarthiApp.kt                  // @HiltAndroidApp
├── MainActivity.kt                // single activity, hosts NavHost
├── core/
│   ├── network/                   // Retrofit, OkHttp, interceptors, CertificatePinner
│   │   ├── AuthInterceptor.kt      // adds Authorization: Bearer <token>
│   │   ├── ErrorInterceptor.kt     // 401 -> session cleared + nav to sign-in
│   │   └── ApiResult.kt            // Success | ApiError | NetworkError | Offline
│   ├── session/                   // token storage (EncryptedSharedPreferences), SessionManager
│   ├── provenance/                // Provenance enum + badge composable
│   ├── i18n/                      // runtime translation engine + on-device cache (§14)
│   ├── connectivity/              // NetworkMonitor as a Flow<Boolean>
│   ├── design/                    // Material 3 theme, tokens, typography, components
│   └── util/                      // formatters: ₹, dates (IST), units, Devanagari-safe text
├── data/
│   ├── remote/                    // one Retrofit service interface per router (§16)
│   ├── local/                     // Room entities, DAOs, the pending-write queue
│   ├── ble/                       // scanner, GATT client, SensorLineParser (§11)
│   └── repository/                // one repository per domain; the ONLY layer the UI talks to
├── domain/                        // models + use-cases; no Android imports in here
└── ui/
    ├── nav/                       // routes, bottom bar, mic FAB
    ├── auth/                      // S1-S8 (§5)
    ├── home/  farm/  money/  ask/  me/   // the five tabs (§6)
    └── common/                    // LoadingState, EmptyState, ErrorState, OfflineBanner
```

**Rules**
- A composable never calls Retrofit or Room. ViewModel → repository → remote/local.
- Every repository method returns `ApiResult<T>`; the UI renders all four states.
- `domain/` has zero Android dependencies so the parsers and rules are unit-testable.
- No `GlobalScope`. All work in `viewModelScope` or a WorkManager worker.
- Configuration changes and process death must not lose screen state (`SavedStateHandle`).

---

## 4. The non-negotiable rules

These are what make Saarthi trustworthy. Breaking any of them is a bug, not a
preference.

1. **Never invent a number.** If a service is unavailable, show *why* — "Could not
   reach the market service" — not a stale or plausible figure, not a placeholder, not
   a dash that reads like zero.
2. **Every number carries provenance.** Render it as a small badge next to the value:
   `real_sensor` · `device_simulated` · `farmer_reported` · `simulated` · `lab` ·
   `live_api` · `knowledge_base` · `estimated` · `unavailable`.
   `device_simulated` matters most: the packet genuinely arrived over BLE, but the
   device generated the number itself. That is neither "measured" nor "simulated by
   us", and collapsing it into either is how a demo figure gets quoted as a real
   measurement. Model it as a sealed class, not a string, so a new value cannot be
   silently rendered as blank.
3. **Four agent states, each shown differently:** `ok`, `needs_input` (the farmer can
   fix it — ask them), `unavailable` (*we* have the gap — never ask the farmer for
   something no typing could fix), `error`.
4. **An English label is a minor annoyance; a blank one is a broken app.** A failed
   translation falls back to English, never to empty string.
5. **Never ask for data the farmer already gave.** If the field record holds the crop,
   do not ask for the crop.
6. **Offline is a state, not an error.** Show cached data with its age, clearly marked.
7. **Never imitate a government portal and never collect credentials for one.** Scheme
   screens link out to the official site in a Custom Tab.

---

## 5. Authentication and accounts (build this first)

### 5.1 The account model

- One account = one **phone number** (10 digits, India) + one **email**. The phone
  number is the identity that joins the dashboard, the app and the phone-call channel:
  a fact told to the voice bot appears in the app, and the reverse.
- Roles: **`user`** and **`admin`**. Admin unlocks the Admin screen (§6.19).
- Status: `pending` (registered, unverified) · `active` · `locked` · `disabled`.
- Passwords: **scrypt** (N=2^14, r=8, p=1) with a per-user salt, server-side. Session
  tokens are **SHA-256** digests. The app never stores, logs or caches a raw password
  beyond the lifetime of the text field.
- **Server-side constants the UI must match exactly:**

| Constant | Value |
|---|---|
| Session lifetime | **7 days** |
| OTP validity | **10 minutes** |
| OTP max wrong attempts | **5** |
| OTP resend cooldown | **60 seconds** |
| Failed sign-ins before lockout | **5** |
| Lockout duration | **15 minutes** |

> The earlier React-Native spec said a 30-second resend cooldown. **The backend enforces
> 60.** Use 60, or the Resend button will be live for 30 seconds during which every tap
> is rejected with a wait time.

### 5.2 Screens

**S1 — Splash / boot**
Read the stored token → `GET /api/auth/me`. If `signed_in: true` → Home. Otherwise →
Welcome. Use the Android 12+ **SplashScreen API** with `keepOnScreenCondition`, capped
at **2 seconds**; on timeout go to Welcome rather than hanging. Never show a spinner
with no exit.

**S2 — Welcome**
**Language picker first** (13 languages, each in its own script), then `Sign in` /
`Create account`. The language choice must be possible *before* sign-in — a farmer who
cannot read English cannot find the sign-in button otherwise. Persist the choice to
DataStore immediately, before any account exists.

**S3 — Create account**
Fields: full name · phone (10 digits) · email · password · confirm password ·
preferred language.
- `GET /api/auth/captcha` → render the challenge text, send `challenge_id` + `answer`
  with the registration call.
- `POST /api/auth/register` `{full_name, phone, email, password, language, challenge_id, answer}`
  → the server sends one 6-digit OTP to **both email and SMS**.
- Client-side validation before the call: phone matches `^[6-9]\d{9}$`, email is
  plausible, password meets the server's rule (surface the server's own
  `password_problem` text verbatim when it rejects), passwords match.
- → S4.

**S4 — Verify code (sign-up)**
Six separate digit boxes with auto-advance, backspace-to-previous, full paste support,
and `autofillHints = SMS_OTP_CODE` so Android's SMS Retriever can fill it.
- `POST /api/auth/verify` `{phone, code}` → on success the account is `active` **and
  signed in** (the response carries the session token). → S6.
- `POST /api/auth/resend` behind a visible 60 s countdown. When the server returns a
  `wait` value, honour the server's number, not the local timer.
- A wrong code shows **attempts remaining** ("That code is not right. 3 attempts
  left."), never a vague "invalid credentials".

**S5 — Sign in (two-step, OTP-gated)**
1. `POST /api/auth/login` `{phone_or_email, password}` → **this does not sign you in.**
   It validates the password and sends an OTP.
2. `POST /api/auth/login-verify` `{phone, code}` → returns the session token.

Store the token in **EncryptedSharedPreferences**. Send it as
`Authorization: Bearer <token>`. If the account is locked, show the server's own
sentence including the minutes remaining.

**S5a — Forgot password**
- `POST /api/auth/forgot` `{phone_or_email}` — the server **answers identically whether
  or not the account exists**, and the UI must not leak the difference either: the same
  success screen, the same wording, the same timing, always.
- `POST /api/auth/reset` `{phone, code, new_password}`.

**S5b — Change password** (inside Profile)
`POST /api/auth/change-password` `{current_password, new_password}`. On success, tell the
farmer that other devices stay signed in (or are signed out, whichever the server does)
— do not leave them guessing.

**S6 — Onboarding (first run only, every step skippable)**
1. Village, tehsil, district, state, pincode
2. Total land (acres), soil type, irrigation method, farming since
3. **Add your first field** — name, crop, sowing date, area, and a location (drop a pin
   on the OSM map or use GPS)
4. Permissions explainer (§19) — explanation only, no system dialogs yet

All of it is editable later in Profile. A farmer who skips everything still gets a
working app, just with more "I need to know X" answers from the agents.

**S7 — Sign out**
`POST /api/auth/logout`, then clear the token, the Room cache, the FCM token
registration and every cached farm value. Signing out must leave nothing of the
previous farmer on the device.

**S8 — Biometric unlock (optional)**
`BiometricPrompt` on top of a *stored* session, as a convenience. It is never a
replacement for the session and never a second auth factor.

### 5.3 Session rules

- On **any 401**: clear the session, navigate to S5, show "Your session expired, please
  sign in again." Never silently fail, never retry in a loop.
- Sessions survive app restarts and process death. Do not force a re-login on cold start.
- One `ErrorInterceptor` owns this. No per-screen 401 handling.

---

## 6. Navigation and screens

Bottom navigation, **five tabs** — more than five is unusable at this screen size. Each
tab is its own nested nav graph with its own back stack.

| Tab | Screens |
|---|---|
| **Home** | Overview · Pipeline Results · (Run advisory) |
| **My Farm** | Soil Health · Weather · Crop Advisor · Pest & Disease · My Fields · Sensor |
| **Money** | Market Prices · Farm Finance (Ledger) · Govt Schemes · Farm Support |
| **Ask** | AI Assistant (chat) · Voice Calls · Call-back Tasks |
| **Me** | Profile & Settings · Reports · AI Agents · Admin (admins only) |

A persistent **microphone FAB** floats above the bottom bar on every screen — one tap to
ask Saarthi anything by voice. This is the primary input method for low-literacy users
and must never be more than one tap away. Hold to talk, release to send.

19 screens in total. Every one of them needs all four states from §17.

### 6.1 Overview (Home)

`GET /api/dashboard/overview?parcel_id=<id>`

- **Greeting** with the farmer's real name and the time of day.
- **Field selector** pinned at the top — every card below reflects the *selected* field.
  Each field has its own crop, its own coordinates and therefore its own weather. The
  selection is app-wide state, not per-screen.
- **Four summary cards:** soil health index · weather now · crop condition · market price.
- **The 15-agent chain diagram** (§10) — the signature visual.
- **Recent activity** list.
- **Run Advisory** button — exactly one, not two.
- **Fetch data** button for the sensor.

> **Known bug not to port:** the web build painted a stale pre-filled name and village
> until the profile icon was tapped. Load the real profile **before first paint**, and
> show a skeleton rather than wrong-but-plausible text.

### 6.2 Pipeline Results

`POST /api/pipeline/run` · `POST /api/pipeline/stop` · `GET /api/pipeline/progress` ·
`GET /api/pipeline/last` · `GET /api/pipeline/status` · `GET /api/pipeline/inputs` ·
`GET /api/pipeline/agent-status`

- Agent 15's integrated advisory, in full, **in the farmer's language**.
- Per-agent expandable card: headline · findings · actions · assumptions · evidence ·
  provenance · state badge.
- Agents that need input show an inline form, and those inputs are **collected before
  the chain runs**, not after it fails. `GET /api/pipeline/inputs` tells you what is
  missing.
- **Force-stop** control that actually calls `POST /api/pipeline/stop`. On Android,
  cancelling a coroutine frees the screen but leaves the server working — the stop call
  is the only thing that ends the run.
- Progress polling at **1–2 s** while a run is live, stopped the moment it is not.
  Suspend polling in `onStop` and resume in `onStart`; never poll a backgrounded app.
- Export the advisory to PDF (§6.16).

### 6.3 Soil Health

`GET /api/sensors/latest` · `GET /api/sensors/history` · `GET /api/soil/background`

Seven parameters, each with value, unit, agronomic band and provenance badge:

| Parameter | Unit | Plausible range | Typical agronomic band |
|---|---|---|---|
| Nitrogen (N) | mg/kg | 0 – 2000 | 20 – 60 |
| Phosphorus (P) | mg/kg | 0 – 2000 | 10 – 25 |
| Potassium (K) | mg/kg | 0 – 2000 | 120 – 280 |
| Soil temperature | °C | −10 – 60 | 15 – 35 |
| Soil moisture | % | 0 – 100 | 25 – 60 |
| EC | dS/m | 0 – 10 | 0.2 – 1.6 |
| pH | pH | 3 – 10 | 6.0 – 7.5 |

- A reading older than **6 hours** is marked **stale**, visibly, on every screen that
  shows it — not only here.
- Trend chart per parameter from `/api/sensors/history`.
- A "where this came from" line distinguishing **SoilGrids background** (a regional
  model) from **the farmer's own probe** (their field). These must never be averaged
  together into one number.
- A value outside the plausible range is shown as rejected with the reason, not clamped.

### 6.4 Weather

`GET /api/weather?parcel_id=<id>`

Source: **Open-Meteo**, for the *selected field's* coordinates — not a district
centroid, and not the phone's GPS position unless the farmer explicitly asks for that.

Current conditions plus a **7-day forecast**: max/min temperature, rain mm, rain
probability, wind, ET₀. Derived advisories: spraying window · irrigation need · frost
risk · heat stress.

### 6.5 Crop Advisor

`GET /api/crops?season=rabi|kharif|zaid`

Cards in a grid, scored **0–100** by Agent 3 from the farmer's soil pH, EC, the season
and water availability. **The score ranks crops against each other — it is not a yield
prediction, and the screen must say so in plain words.**

Catalogue (18 profiles):
- **Rabi (12):** wheat · mustard · chickpea · onion · lentil · field pea · garlic ·
  coriander · fenugreek · maize · barley · potato
- **Kharif (4):** rice · tomato · maize · cotton
- **Zaid (4):** tomato · watermelon · muskmelon · cucumber

Card face: duration (days) · water need (mm) · sowing window. On tap, the full profile:
pH band · EC tolerance · seed rate · row spacing · sowing depth · and the **stage
timeline with Kc values and a note per stage**.

> Grid rule: 2 columns on a phone, 4 on a tablet/landscape. Keep each season's count a
> multiple of the column count or the last row has holes.

### 6.6 Pest & Disease

`POST /api/vision/diagnose` (multipart image) · `GET /api/vision/status`

- **Camera-first.** Open CameraX, not a file picker. Offer gallery as the secondary
  action.
- On-device framing guidance before the shot: fill the frame with one leaf, daylight,
  hold steady. Show a framing overlay.
- Model: EfficientNetB0, **32 classes**, trained on 39.5 k images, exported to ONNX,
  run **server-side**. Do not bundle the model in the APK.
- **Confidence threshold 0.90.** Below it the app **refuses to name a disease** and says
  what to do instead. This is deliberate and must not be softened into a "best guess" —
  the model would otherwise force a mango leaf, a hand or a wall into one of its 32
  classes. Also surface the crop gate: if the chosen crop does not match the image, say
  so rather than naming a disease of another crop.
- Always pair any diagnosis with: *confirm with your local agriculture officer before
  spraying.*
- Compress to ≤ 1600 px on the long edge and ~85 % JPEG before upload — a full 12 MP
  photo over 2 G will time out.
- Keep a **local history** of past diagnoses with their photos (Room + app-private file
  storage).

### 6.7 Govt Schemes

`GET /api/schemes`

7 schemes, ranked for this farm, each with category · subsidy · eligibility · documents
needed · how to apply · official portal link:

PM-KISAN · Kisan Credit Card (KCC) · PMKSY micro-irrigation · PMFBY crop insurance ·
Soil Health Card & fertiliser subsidy · SMAM mechanisation · PM-KUSUM solar pump

Filter by category. Bundle this list in the APK as a fallback so it works fully offline.
**Never collect credentials and never imitate a government portal** — open the official
site in a Chrome Custom Tab.

### 6.8 Farm Finance (Ledger)

`GET /api/ledger` · `POST /api/ledger` · `DELETE /api/ledger/{entry_id}` ·
`GET /api/ledger/summary` · `GET /api/finance`

- Record an **expense** or **income**, per field, per season, in one of nine categories:
  **seed · fertiliser · pesticide · irrigation · labour · machinery · transport ·
  storage · other**. A deliberately short list — a long one is a list nobody picks from
  on a phone, and "other" with a note beats a wrong category.
- The **season is computed on write and stored**, so changing the season logic later
  cannot silently re-file old entries.
- Totals: Spent · Earned · Ahead/Behind by.
- **Voice entry:** "I spent 4200 on urea", "majdoor ko 1800 diye", "sold the wheat for
  12500". The parser is deliberately narrow — a digit run plus a category word.
  Spelled-out amounts ("four thousand") are **refused rather than guessed**, because
  filing a wrong amount silently is worse than asking again. Always show the parsed
  amount and category for confirmation before saving.
- An empty record reports zeros and says the record is empty. **There is deliberately no
  district cost-of-cultivation fallback** — that number would be read back as the
  farmer's own spending.
- **Offline-queued (§8).** This is the single most important offline case: a farmer
  typing a cost while standing in a field with no signal must not lose it.

### 6.9 Market Prices

`GET /api/market?commodity=&state=&district=&parcel_id=`

Live **data.gov.in Agmarknet**. Defaults to the selected field's district, then widens
**district → state → national**, and **reports which scope it used** — "the price in your
district" and "the price somewhere in India" are very different numbers to sell on.

Shows median modal price · highest with its market name · record count · arrival date.
Nearest markets are ordered by **district match then price — not distance**, because
Agmarknet records carry no coordinates and a computed distance would be invented.

Always show: *prices are as reported by the listed markets on the stated arrival date,
in ₹/quintal. Past prices do not predict future prices.*

### 6.10 Farm Support

`GET /api/support`

5 national helplines and 5 official portals, each tappable (`Intent.ACTION_DIAL` — dial,
never auto-call — and a Custom Tab). Says plainly that the **block agriculture officer**
is the local authority.

### 6.11 AI Assistant (chat)

`POST /api/chat` · `GET /api/chat/history` · `GET /api/chat/quick-prompts` ·
`GET /api/chat/session/{id}` · `POST /api/chat/session/{id}/language` ·
`POST /api/rag/search` · `POST /api/intent`

- RAG-backed over the farm knowledge base, plus the farmer's live farm context.
- **20 intents**, routed rule-first then LLM: `soil_health` · `nutrient` · `irrigation` ·
  `crop_selection` · `crop_stage` · `weather` · `pest_disease` · `weed` · `seed_sowing` ·
  `crop_protection` · `market` · `economics` · `scheme` · `finance` · `support` ·
  `ledger` · `task` · `residue` · `sensor` · `general`.
- Accepts **English, Hinglish and Devanagari**. The router carries a Devanagari stem
  table — spoken Hindi questions must route to the right intent, not fall through to
  small talk. Send the text exactly as typed or transcribed; do not transliterate on the
  device.
- Typed or spoken. Every reply gets a **play button** for TTS.
- Shows which agents were consulted and the evidence behind the answer.
- Quick-prompt chips for common questions.
- Per-farmer memory keyed on the **last 10 digits of the phone number** — so a fact told
  on a phone call shows up here.
- Keep the transcript in Room so the conversation survives a cold start and is readable
  offline.

### 6.12 Voice Calls

`POST /api/calls/outbound` · `POST /api/calls/cancel` · `GET /api/calls` ·
`GET /api/calls/{id}` · `GET /api/calls/status` · `GET /api/calls/ivr/languages` ·
`POST /api/calls/simulate`

Saarthi calls the farmer on an ordinary phone — **this is the feature that reaches
farmers with no smartphone at all**, and the app is its control panel.

- Request a call-back; the delay is configured in Profile.
- **One call at a time.** The guard is server-side, and the app must show **why** a
  second request was refused, in the server's own words.
- Call log with honest per-status labels. A call is reported as `requested` **only once
  Twilio has accepted it**, never merely because n8n returned 200.
- Phone numbers are normalised to **E.164** (`+91…`).
- **Force end call — three outcomes, reported as three.** Saarthi cannot hang up Twilio
  directly (the auth token lives only in n8n's encrypted credential store):
  - *Booked, not dialled* → genuinely cancelled, nothing rings.
  - *Connected* → the next turn of the call receives `end_call: true` and Twilio hangs
    up within a few seconds. Status must read **"Ending — hangs up at the next turn"**,
    not "Cancelled", so it never claims the call is over while the farmer can still hear
    it.
  - *Ringing, unanswered* → no turns exist to carry the instruction, so it rings out,
    and the app says exactly that.
- IVR keypad language menu, **DTMF 1–13**, shown so the farmer knows which digit is
  their language before the call arrives.

### 6.13 Call-back Tasks ⭐

`GET /api/tasks` · `POST /api/tasks` · `DELETE /api/tasks/{task_id}`

Book Saarthi to **ring you later**. One plain-language box, parsed by the same parser the
phone line uses — so any phrase that works on a call works here.

**Three kinds:**

| Kind | Example | Behaviour |
|---|---|---|
| `reminder` | "remind me in 1 hour to irrigate field 1" | Says the farmer's own words back |
| `question` | "tell me the wheat price after 30 minutes" | **Looks it up when the phone rings, not when you book it** |
| `watch` | "call me when wheat goes above 2400" · "call me if rain is coming" | Re-checked every 20 s until it fires or expires after **7 days** |

**The fire-time resolution is the whole point.** A price asked for "in 30 minutes" is
fetched in 30 minutes. Resolving at booking time would hand the farmer a half-hour-old
number while sounding completely certain.

Parses English, Hinglish and Devanagari: "after 30 minutes", "30 minute baad",
"१ घंटे बाद", "kal subah", "shaam 6 baje", "half an hour".

Rules the app must surface:
- The confirmation **names the time back** — the farmer's only check that the parser
  heard the right number. Show it before and after booking.
- An unparseable request **books nothing** and asks "when?" — never a half-booking that
  looks saved.
- A failed lookup at fire time says so, with **no digits in the sentence**.
- A watch that never triggers **calls to say it has stopped** — an unkept promise the
  farmer never hears about is worse than a refusal. Show the 7-day expiry on the card.
- **Quiet hours 21:00–06:30 IST apply only to moments nobody chose** — a watch
  triggering overnight. A time the farmer explicitly asked for is honoured at any hour,
  because night irrigation is routine when pump electricity is supplied at night. Do not
  "helpfully" extend quiet hours to booked times.

### 6.14 My Fields

`GET /api/profile/fields` · `POST /api/profile/fields` ·
`DELETE /api/profile/fields/{field_id}` · `GET /api/farm`

Per field: name · crop · sowing date · area · soil type · irrigation method · mapped
location.

Map (osmdroid, **OpenStreetMap tiles**): zoom · fullscreen · **current location** ·
**registered land locations**. Coordinates stored at **6 decimal places** — 3 dp is
~100 m, which is a different field. Cache tiles to app-private storage so a previously
viewed field map opens offline.

### 6.15 Sensor

`POST /api/sensors/ble` · `POST /api/sensors/manual` · `POST /api/sensors/ingest` ·
`GET /api/sensors/latest` · `GET /api/sensors/history` · `GET /api/sensors/register-map`

See §11 for the full BLE contract. Screen behaviour:

- Connection state, device name, signal, and a **Fetch** button.
- **Fetch-only updates.** Values change when the farmer presses Fetch, never silently in
  the background. A farmer looking at a number must be able to trust it is the one they
  asked for.
- Manual entry as a fallback, saved as `farmer_reported`.
- The sensor path shown as a single horizontal line above the cards:
  **probe → Arduino → HM-10 → phone → Saarthi**.
- `DEVICE_SIMULATED` is shown plainly whenever the Arduino is generating values rather
  than reading the probe.
- The register map from `/api/sensors/register-map` is marked **provisional — confirm
  against the sensor manual before field use**; show that caveat, do not hide it.

### 6.16 Reports

`GET /api/reports` · `GET /api/reports/{report_id}?format=json|pdf|xlsx`

Four reports — **`soil`** (Soil health report) · **`advisory`** (Advisory history) ·
**`calls`** (Call log) · **`sensor-history`** (Sensor history) — each downloadable as
**PDF or Excel**, then shared through the OS share sheet. WhatsApp is how these
documents actually travel between a farmer and an officer.

Save into app-private storage and share via **FileProvider** (do not request legacy
storage permissions). Offer "Save to Downloads" through `MediaStore` as a secondary
action.

### 6.17 AI Agents

`GET /api/agents` · `GET /api/agents/routing` · `POST /api/agents/run`

The 15 agents with their dependencies and inputs (§10). Run a single agent on demand.
Architecture diagram. Make clear that Conversational AI, the LLM, RAG, the orchestrator,
the sensor transport and the delivery layer are **shared capabilities, not agents** —
there are exactly 15 agents and the backend asserts it at import time.

### 6.18 Profile & Settings

`GET /api/profile` · `POST /api/profile` · `DELETE /api/profile/memory` ·
`DELETE /api/history` · `GET|POST /api/farmers/{phone}` ·
`DELETE /api/farmers/{phone}/facts/{key}`

Personal details · farm details · language · **dark mode** (system / light / dark) ·
call-back delay · notification preferences · change password · sign out.

**Data controls:**
- Clear advisory / call / session history, and clear remembered facts.
- `DELETE /api/history?what=all` clears advisories, calls and sessions **but not
  readings**. Readings require `what=readings` by name, because a farmer clearing a
  question log would never expect their soil history to go with it. Make the two
  destructive actions visibly separate, each with its own confirmation naming exactly
  what will be deleted.

### 6.19 Admin (role = `admin` only)

`GET /api/admin/users` · `GET /api/admin/users/{id}` ·
`POST /api/admin/users/{id}/role` · `POST /api/admin/users/{id}/status` ·
`DELETE /api/admin/users/{id}?purge=true|false` · `GET /api/admin/otp-status` ·
`GET /api/keys/status`

User list · role change · status change · delete (with an explicit choice about purging
that user's farm data) · OTP delivery health · service-key status.

**Hide the tab entirely for non-admins** — do not merely disable it. A disabled admin tab
tells every farmer that an admin surface exists.

---

## 7. The 13 languages

English + 12 Indian languages. Each carries an Azure locale, a neural voice and a keypad
digit for the feature-phone IVR:

| # | Language | Native | Locale | DTMF |
|---|---|---|---|---|
| 1 | English | English | en-IN | 1 |
| 2 | Hindi | हिन्दी | hi-IN | 2 |
| 3 | Bengali | বাংলা | bn-IN | 3 |
| 4 | Tamil | தமிழ் | ta-IN | 4 |
| 5 | Telugu | తెలుగు | te-IN | 5 |
| 6 | Marathi | मराठी | mr-IN | 6 |
| 7 | Gujarati | ગુજરાતી | gu-IN | 7 |
| 8 | Kannada | ಕನ್ನಡ | kn-IN | 8 |
| 9 | Malayalam | മലയാളം | ml-IN | 9 |
| 10 | Punjabi | ਪੰਜਾਬੀ | pa-IN | 10 |
| 11 | Odia | ଓଡ଼ିଆ | or-IN | 11 |
| 12 | Assamese | অসমীয়া | as-IN | 12 |
| 13 | Urdu | اردو | ur-IN | 13 |

Bundle the fonts (or verify Noto coverage on minSdk 24 devices) — a language rendered as
tofu boxes is worse than English.

---

## 8. Offline architecture

The web app assumes a connection. **The Android app must not.**

| Feature | Offline behaviour |
|---|---|
| Last advisory | Cached in full, readable, stamped "from 2 hours ago" |
| Soil readings | Cached; **BLE still works offline** — the sensor is local, the phone is the gateway |
| Ledger entries | **Queued in Room and synced by WorkManager when back online.** Never lost, never silently dropped |
| Weather / market | Cached with age shown; refuses to present anything older than 24 h without saying so |
| Voice chat | Needs a connection — say so plainly and offer "book a call-back" instead |
| Crop profiles, schemes, helplines | **Bundled in the APK**, fully offline — they change rarely |
| Pipeline run | Needs a connection |
| UI translations | Cached on device (§14) — second launch is instant and offline |

**Implementation**
- Room is the single source of truth for every list the farmer can read offline.
  Repositories emit from Room and refresh from the network (stale-while-revalidate).
- A `pending_writes` table holds queued ledger entries and manual readings with a
  client-generated UUID for **idempotency**, so a retry cannot double-file a cost.
- One `SyncWorker` (`WorkManager`, `NetworkType.CONNECTED`, exponential backoff) drains
  the queue. Show each queued row as "waiting to sync" in the UI.
- A persistent **non-blocking banner** when offline. **Never a modal** — a farmer in a
  field cannot dismiss a dialog to see their own data.

---

## 9. Provenance vocabulary

Model this as a sealed class in `core/provenance/`, never a raw string, so an unknown
value cannot render as blank. Every numeric value on every screen maps to exactly one:

| Value | Means | Badge wording |
|---|---|---|
| `real_sensor` | The RS485 probe measured it | "measured" |
| `device_simulated` | The BLE packet is genuine; the Arduino generated the number | "device-generated" |
| `farmer_reported` | The farmer typed it | "you told us" |
| `lab` | A soil-testing laboratory result | "lab test" |
| `live_api` | Fetched live from Open-Meteo / Agmarknet / SoilGrids | "live" |
| `knowledge_base` | From the bundled agronomic knowledge base | "reference" |
| `estimated` | Derived by arithmetic the agent shows | "estimated" |
| `simulated` | Generated by us for a demo | "simulated" |
| `unavailable` | We could not get it — **and no number is shown** | "not available" |

Two rules the UI enforces: `estimated` always exposes the arithmetic behind it on tap,
and `unavailable` never appears next to a figure — the figure is absent instead.

**Separately**, a reading's coordinates carry their own source, which is *not* soil
provenance and must not be merged with it: `gps` · `browser` · `manual` · `field_centre`.

---

## 10. The 15-agent pipeline

Five dependency waves. Conversational AI, the LLM, RAG, the orchestrator, the sensor
transport and the delivery layer are **shared capabilities, not agents** — do not draw
them as nodes.

| # | Agent | Purpose | Depends on |
|---|---|---|---|
| 1 | Soil Sensor Acquisition and Data Quality | Acquire, decode and validate the seven sensor fields with provenance | — |
| 2 | Soil Health and Interpretation | Interpret soil values and identify likely constraints | 1 |
| 3 | Crop Suitability and Selection | Rank crops that fit this farm, season, soil and water | 2 |
| 4 | Crop Growth Stage and Agronomy | Establish the crop stage and the management it calls for | — |
| 5 | Weather and Climate Risk | Assess weather risk for the crop and for field operations | — |
| 6 | Nutrient Management and Fertiliser | Evidence-based nutrient guidance with transparent arithmetic | 1, 2, 4 |
| 7 | Irrigation and Water Management | Decide whether to irrigate and how much water to apply | 1, 2, 4, 5 |
| 8 | Pest and Disease Risk | Narrow the causes of a symptom and say what to check next | 4, 5 |
| 9 | Weed Management | Weed decisions across cultural, mechanical and chemical options | 4 |
| 10 | Seed, Sowing and Crop Establishment | Seed rate, sowing window, spacing, establishment | 3, 5 |
| 11 | Crop Protection and Safe Application | Check any protection action is justified, safe and label-backed | 8, 9, 5 |
| 12 | Agricultural Market Intelligence | Traceable mandi prices with date and source | — |
| 13 | Farm Economics and Profitability | Costs vs returns, using the farmer's own numbers | 12 |
| 14 | Sustainable Farming and Crop Residue | Residue management and soil conservation | — |
| 15 | Integrated Advisory and Recommendation Validation | Combine and validate before delivery | 1–14 |

**Chain animation rules** (this is the demo's signature visual — get it exactly right):
- Two rows, both left-to-right, centred; **agent 9 sits under agent 1**.
- START and FINISH are **red until the run actually reaches them**. They were once green
  on load, which told the viewer the run had finished before it began.
- Nodes turn green **in order**, in a smooth slow transition, **synced to real progress**
  from `GET /api/pipeline/progress`. Never a fake timed animation.
- On error, **freeze the chain exactly where it got to** and raise a notification that the
  chain aborted. Never leave it half-lit with no explanation.
- Implement with Compose `animateColorAsState` driven by real progress state, and respect
  the system "remove animations" accessibility setting.

---

## 11. BLE soil sensor (native Android — a real upgrade over the web build)

**Hardware path:** RS485 7-in-1 soil probe → Arduino Uno → **HM-10 BLE module** → phone.

| Thing | Value |
|---|---|
| Service UUID | `0000ffe0-0000-1000-8000-00805f9b34fb` |
| Characteristic UUID | `0000ffe1-0000-1000-8000-00805f9b34fb` |
| Transport | Notify + write, ASCII lines |

**Native advantages to use:** background scanning, auto-reconnect, MTU negotiation, and
no browser permission dance. The web build was limited to Web Bluetooth; do not port
those limitations.

**Parsing contract — this is where a bug becomes a wrong fertiliser recommendation:**
- **Label-only parsing. No positional fallback, ever.** Positional parsing once filed
  phosphorus as nitrogen. If a label is missing, that field is simply **absent** — not
  zero, not inferred from position.
- Strip trailing unit tokens: `EC_US_CM` → field `ec`, unit `us/cm`.
- Use **two lookaheads** so `LAT: 27.1 LON: 78.0` cannot read "LO" as a unit and file
  longitude as nitrogen.
- Lines arrive fragmented across BLE notifications — buffer and split on newline before
  parsing. Never parse a partial line.
- Put the parser in `domain/` with **no Android imports**, and unit-test it against the
  real captured output, including the malformed cases. A `ble-parse.test.js` equivalent
  exists on the web side; port those cases to JUnit.

**Reading modes and provenance:**
- `real_sensor` — the probe measured it.
- `DEVICE_SIMULATED` — the packet genuinely arrived over BLE but the Arduino generated
  the number. Surface this plainly; it is currently the common case while the probe is
  being wired.
- `farmer_reported` — typed by hand.
- Location source is tracked separately from soil provenance: `gps` · `browser` ·
  `manual` · `field_centre`.

**Upload:** `POST /api/sensors/ble` with the parsed fields and the raw line. The server
validates ranges and freshness (6 h) and is the authority on what is acceptable.

**Permissions:** §19. On Android 12+ use `BLUETOOTH_SCAN` with
`android:usesPermissionFlags="neverForLocation"` plus `BLUETOOTH_CONNECT`; below 12 you
need `ACCESS_FINE_LOCATION` for a scan to return results at all. Handle "Bluetooth off"
and "Location services off" as distinct, fixable states with a button that opens the right
system settings page.

---

## 12. Camera and vision

- **CameraX** `Preview` + `ImageCapture`. Open the camera directly; gallery is secondary.
- Framing overlay, a daylight hint, and a steadiness check before the shutter.
- Downscale to ≤ 1600 px long edge, JPEG ~85 %, strip EXIF location before upload.
- Upload multipart to `POST /api/vision/diagnose` with the crop the farmer selected.
- Render: top-3 predictions with confidence, the **0.90 threshold refusal** when below,
  the crop-gate mismatch message, and the agriculture-officer caveat.
- Store the photo app-privately and link it to the Room diagnosis record.

---

## 13. Speech

`POST /api/speech/tts` · `POST /api/speech/stt` · `GET /api/speech/status` ·
`POST /api/speech/token` · `GET /api/speech/audio/{name}` · `POST /api/voice/transcribe` ·
`POST /api/voice/turn` · `GET /api/voice/status` · `POST /api/voice/end`

- **Sarvam AI** provides Indian-language TTS (`bulbul`) and STT (`saarika`).
- **Azure Speech exists in the codebase but is disabled. Leave it off.** Do not add an
  Azure path "for reliability" — a silent substitution of a different language is exactly
  what the backend refuses to do.
- **Do not use Android's on-device `SpeechRecognizer`.** Its Indian-language coverage is
  inconsistent and it would bypass the server's language contract. Record audio, send it
  up.
- Capture with `AudioRecord`: **16 kHz, mono, 16-bit PCM**, wrapped as WAV. Show a live
  waveform and **a visible transcript so the farmer can see what was heard before it is
  sent**.
- Hold-to-talk on the mic FAB. Release to send, swipe away to cancel.
- Every AI answer has a **play button**. Autoplay only if the farmer opted in.
- Play TTS through Media3 with `AudioAttributes` set to speech, request audio focus, and
  duck/pause correctly when a call arrives.
- Enrolment-by-voice flow: `GET /api/enrol/steps` · `POST /api/enrol/turn` ·
  `POST /api/enrol/confirm` — a farmer can be enrolled entirely by speaking.

---

## 14. Interface translation (not Android string resources)

`GET /api/i18n/languages` · `POST /api/i18n/translate` · `GET /api/languages`

**This is the one place where the Android-idiomatic answer is the wrong answer.** Do not
ship 13 `values-xx/strings.xml` catalogues. The interface is translated **by the LLM with
a server-side cache**, so a new screen is translated without a release, and every user
shares the same cache.

- Keep **English as the only source** in `strings.xml`. Pass each English string through a
  translation layer that checks the on-device cache, then the server, then falls back to
  English.
- Send strings in **chunks of ~40**, **max 6 concurrent requests**. Firing them all at
  once trips the model API's rate limit, and a rate-limited chunk silently falls back to
  English.
- Anything over **400 characters** goes in a **request of its own**, with a token budget
  scaled by character count — Devanagari costs several tokens per character.
- **Cache on the device** (Room, keyed by `language + source hash`) so the second launch
  is instant and works offline. Prefetch the selected language's strings with WorkManager
  after sign-in.
- **English is never stored.** Switching back to English is an **exact restore of the
  source**, not a re-translation. (On the web build this needed the Latin-letter filter
  bypassed; in Kotlin, keep the original string beside the translated one and swap back.)
- **Never translate:** numbers · units (kg, mm, dS/m, pH, quintal, acre) · scheme names
  and abbreviations (PM-KISAN, KCC, PMFBY, PMKSY, SMAM, PM-KUSUM) · place names · the
  product name *Saarthi* · timestamps · device IDs · and **the language picker's own
  options** — translating that menu into the language just picked leaves no readable way
  back.
- **RTL layout for Urdu** — `supportsRtl="true"`, and use `start`/`end` padding
  everywhere, never `left`/`right`.
- A failed translation shows **English, never blank**.
- Language switching must complete in **under a second** from cache.

---

## 15. Notifications

**FCM** for server-pushed alerts, plus `NotificationManager` channels and an in-app
notification centre (the bell), replacing the web build's localStorage bell.

Triggers:
- Mandi price moved **≥ 5 %** since the last visit
- **≥ 10 mm** rain appearing in the 3-day forecast
- A new government scheme in the catalogue
- A pipeline run aborted or was stopped
- A booked call-back is about to fire, has fired, or failed
- A watch triggered or expired
- Services not configured (**admin only**)

Rules:
- Scheme alerts use a digest of scheme **ids + titles, not contents** — correcting a
  subsidy percentage must not look like a new scheme. There is no government feed, so the
  wording is honestly *"the list on this dashboard has gained something"*, never *"a
  scheme was launched"*.
- Separate channels per category (`prices`, `weather`, `calls`, `advisory`, `system`) so
  a farmer can mute prices without losing call alerts.
- `POST_NOTIFICATIONS` is requested **after the first successful advisory**, not at
  launch — ask once the value is obvious.
- Tapping a notification deep-links to the exact screen, with the back stack synthesised
  correctly.

---

## 16. Full API reference (107 endpoints)

Base URL `https://saarthi2.0.visioncoders.in`. All authenticated calls send
`Authorization: Bearer <token>`. One Retrofit interface per group.

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
`GET /api/schemes` · `GET /api/support` · `GET /api/reports` · `GET /api/reports/{id}` ·
`DELETE /api/history`

**Sensors** — `POST /api/sensors/ble` · `POST /api/sensors/manual` ·
`POST /api/sensors/ingest` · `GET /api/sensors/latest` · `GET /api/sensors/history` ·
`GET /api/sensors/register-map`

**Pipeline & agents** — `POST /api/pipeline/run` · `POST /api/pipeline/stop` ·
`GET /api/pipeline/progress` · `GET /api/pipeline/last` · `GET /api/pipeline/status` ·
`GET /api/pipeline/inputs` · `GET /api/pipeline/agent-status` · `GET /api/agents` ·
`GET /api/agents/routing` · `POST /api/agents/run` · `POST /api/intent`

**Chat & RAG** — `POST /api/chat` · `GET /api/chat/history` ·
`GET /api/chat/quick-prompts` · `GET /api/chat/session/{id}` ·
`POST /api/chat/session/{id}/language` · `POST /api/rag/search` · `GET /api/rag/stats` ·
`POST /api/rag/reindex`

**Calls & voice** — `POST /api/calls/outbound` · `POST /api/calls/cancel` ·
`GET /api/calls` · `GET /api/calls/{id}` · `GET /api/calls/status` ·
`POST /api/calls/turn` · `POST /api/calls/status-callback` · `POST /api/calls/simulate` ·
`GET /api/calls/ivr/languages` · `GET /api/voice/status` · `POST /api/voice/turn` ·
`POST /api/voice/transcribe` · `POST /api/voice/end` · `GET /api/enrol/steps` ·
`POST /api/enrol/turn` · `POST /api/enrol/confirm`

**Tasks** — `GET /api/tasks` · `POST /api/tasks` · `DELETE /api/tasks/{id}`

**Ledger** — `GET /api/ledger` · `POST /api/ledger` · `GET /api/ledger/summary` ·
`DELETE /api/ledger/{id}`

**Vision** — `POST /api/vision/diagnose` · `GET /api/vision/status`

**Speech & i18n** — `POST /api/speech/tts` · `POST /api/speech/stt` ·
`POST /api/speech/token` · `GET /api/speech/audio/{name}` · `GET /api/speech/status` ·
`GET /api/i18n/languages` · `POST /api/i18n/translate` · `GET /api/languages`

**System & admin** — `GET /api/health` · `GET /api/keys/status` · `GET /api/admin/users` ·
`GET /api/admin/users/{id}` · `DELETE /api/admin/users/{id}` ·
`POST /api/admin/users/{id}/role` · `POST /api/admin/users/{id}/status` ·
`GET /api/admin/otp-status`

**Client conventions**
- Timeouts: connect 15 s, read 60 s. The pipeline run and the LLM chat are slow by
  nature — do not set a 10 s read timeout and call it a server bug.
- Retry only idempotent GETs, max 2, with backoff. **Never auto-retry a POST** that
  writes (a ledger entry, a call request, a task booking).
- Log no request bodies in release. `HttpLoggingInterceptor` at `NONE` for release builds.

---

## 17. Error, empty, loading and offline states

Every screen needs all four. Write them as shared composables in `ui/common/`.

| Situation | Message |
|---|---|
| No sensor reading | "No sensor reading yet. Connect your device, or enter values by hand." |
| Service down | "Could not reach the weather service just now." — **and no number** |
| Agent needs input | The agent's own question, with an inline form |
| Agent unavailable | The agent's own explanation — **never a question the farmer cannot answer** |
| Call refused | The server's actual reason, not "something went wrong" |
| Below vision threshold | The refusal plus what to do next — never a guessed disease |
| Offline | A persistent banner, cached data with its age |
| Bluetooth off | "Bluetooth is off" with a button that opens Bluetooth settings |

**Never show a raw HTTP code, a stack trace, an exception class name, or "Error 500" to a
farmer.** Map every error to a sentence a farmer can act on.

---

## 18. Design system

- **Material 3**, with a fixed agricultural palette: greens with earth accents, one tint
  per crop on crop cards. **Disable dynamic colour** — brand consistency matters more
  than wallpaper matching for a demo, and a farmer's wallpaper should not recolour a
  provenance badge.
- **Light and dark mode, both fully supported.** Known traps from the web build: hard-coded
  light gradients on the weather card, dropdowns without an explicit text colour, and
  greeting text vanishing in dark mode. Define every colour as a theme token; no literal
  hex in a composable.
- **Large tap targets — minimum 48 × 48 dp.** Users may be wearing gloves or have
  work-worn hands.
- **High contrast**, legible in direct sunlight (test at full brightness outdoors).
- **Font scaling to 200 %** without breaking layout. Use `sp` for all text, never `dp`.
  No fixed-height text containers.
- **Minimal text.** The web build's single biggest recurring complaint was too much text.
  Every label must earn its place.
- **Icons always paired with text labels**, never icons alone — an unlabelled icon is
  unreadable for a low-literacy user in any language.
- Content descriptions on everything for TalkBack. The app must be usable with a screen
  reader in Hindi.

---

## 19. Permissions

Request each **in context, with a reason shown first**, never all at launch. A denied
permission must degrade gracefully, never block the app.

| Permission | Needed for | Asked when |
|---|---|---|
| `BLUETOOTH_SCAN`, `BLUETOOTH_CONNECT` (API 31+) | BLE soil sensor | First "Connect device" |
| `BLUETOOTH`, `BLUETOOTH_ADMIN`, `ACCESS_FINE_LOCATION` (API ≤ 30) | BLE scan returns nothing without location below Android 12 | First "Connect device" |
| `CAMERA` | Leaf disease photos | First diagnosis |
| `ACCESS_FINE_LOCATION` | Mapping a field, weather for here | Adding a field |
| `POST_NOTIFICATIONS` (API 33+) | Price/weather/call alerts | After the first successful advisory |
| `RECORD_AUDIO` | Voice questions | First mic tap |
| `INTERNET`, `ACCESS_NETWORK_STATE` | Everything | Manifest only |

- Declare `BLUETOOTH_SCAN` with `android:usesPermissionFlags="neverForLocation"` so Play
  does not require a location disclosure for BLE.
- **No storage permissions.** Use app-private files + FileProvider for sharing, and
  `MediaStore` for an explicit "save to Downloads".
- Explain **why** in your own screen before the system dialog appears. Handle
  "don't ask again" by pointing at app settings, not by re-prompting.

---

## 20. Security

- Session token in **EncryptedSharedPreferences** — never DataStore, never
  `SharedPreferences`, never a log line.
- **Certificate pinning** on the API host via OkHttp `CertificatePinner`, with a backup
  pin so a certificate rotation does not brick every installed app.
- `android:allowBackup="false"` and no token in auto-backup.
- **`FLAG_SECURE`** on every password, OTP and admin screen — blocks screenshots and
  excludes them from the recents thumbnail.
- `usesCleartextTraffic="false"`; a network security config that permits no plaintext.
- **No API keys in the APK.** If a build needs one to compile, the design is wrong.
- Clear the token, Room, cached files and the FCM registration on sign-out.
- R8 with the full obfuscation profile for release; keep rules only for serialization
  models.
- **Never build a screen that imitates a government portal or collects credentials for
  one.** Link out.

---

## 21. Build configuration

```kotlin
android {
    namespace = "com.visioncoders.saarthi"
    compileSdk = 35
    defaultConfig {
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        resourceConfigurations += listOf("en")   // translations are runtime, not resources
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            buildConfigField("String", "API_BASE", "\"https://saarthi2.0.visioncoders.in\"")
        }
        debug {
            buildConfigField("String", "API_BASE", "\"https://saarthi2.0.visioncoders.in\"")
        }
    }
    buildFeatures { compose = true; buildConfig = true }
}
```

- Version catalogue for every dependency; no floating versions.
- A **baseline profile** for startup performance on low-end devices.
- Separate `debug`/`release` logging; `Timber` in debug, no-op tree in release.
- Build an **APK**, not only an AAB — a hackathon demo is sideloaded.

---

## 22. Testing

- **Unit (JUnit5):** the BLE line parser (including every malformed capture), the
  call-task "when" parser (English / Hinglish / Devanagari), the ledger voice parser
  (including the spelled-out-amount refusal), the season computation, provenance mapping,
  the translation skip-list.
- **Repository (MockWebServer):** every `ApiResult` branch, the 401 path, the offline path.
- **Flow (Turbine):** pipeline progress state machine, including the frozen-on-error case.
- **Compose UI:** auth flow end to end, offline banner, provenance badges render for every
  enum value, 200 % font scale, dark mode.
- **Manual device matrix:** one Android 7 device, one Android 12 (the BLE permission
  break), one Android 14+ (notification permission), and one low-RAM device.

---

## 23. Acceptance checklist

The build is done when a farmer can:

- [ ] Create an account, verify by a 6-digit OTP sent to both email and SMS, and sign in
      with the **two-step OTP** flow
- [ ] Recover a forgotten password **without the app leaking whether the account exists**
- [ ] Be locked out after 5 failed sign-ins and told how many minutes remain
- [ ] Use the whole app in any of **13 languages**, switched in under a second, and switch
      back to **exact** English
- [ ] Read the app in Urdu with a correct RTL layout
- [ ] Add a field with a crop, a sowing date and a mapped OSM location at 6 dp
- [ ] Connect the HM-10 over BLE and fetch real soil values, **label-matched**
- [ ] See those values on **every** screen, each with a provenance badge, and see
      `DEVICE_SIMULATED` called what it is
- [ ] See a reading older than 6 hours marked stale
- [ ] Run the 15-agent chain and watch it animate **in sync with real progress**
- [ ] Stop a running chain and have the server actually stop
- [ ] See the chain freeze where it failed, with a notification saying so
- [ ] Photograph a leaf and get a diagnosis, or an **honest refusal below 0.90 confidence**
- [ ] Ask a question by voice in Hindi, see the transcript before it sends, and hear the
      answer read back
- [ ] Ask a spoken Hindi question and have it route to the right intent, not small talk
- [ ] Record "I spent 4200 on urea" by voice and see it in the ledger, correctly categorised
- [ ] Have a spelled-out amount refused rather than guessed
- [ ] See live mandi prices for their own district, **with the scope stated**
- [ ] Book "tell me the wheat price in 30 minutes" and **receive a call with the price as
      it is then**
- [ ] Book "call me when wheat goes above 2400" and be called when it happens
- [ ] Be told when a watch expires after 7 days without firing
- [ ] Have a 6 a.m. booked call honoured, while an unasked-for 2 a.m. watch waits for morning
- [ ] End a live call and see honestly which of the three outcomes happened
- [ ] Download a soil report as PDF **and** Excel and share it on WhatsApp
- [ ] Use the app with **no signal** and lose nothing they typed
- [ ] Clear their question history without losing their soil readings
- [ ] Sign out and leave no farm data on the device
- [ ] Never once see a number the system could not justify

---

## 24. Build order

Ship in this order. Steps 1–4 are the demo spine — they are what prove the system is
real.

1. **Auth** (S1–S8) + API client + token storage + i18n shell + theme
2. **Profile, fields, osmdroid map**
3. **Overview + BLE sensor + Soil Health** (with provenance badges from the start)
4. **Pipeline chain + Pipeline Results** (real-progress animation, force stop)
5. **Weather, Crop Advisor, Market Prices**
6. **Chat + speech** (STT capture, TTS playback)
7. **Voice Calls + Call-back Tasks**
8. **Ledger + the offline write queue**
9. **Vision, Schemes, Support, Reports**
10. **Notifications, Admin, accessibility and polish**

---

## 25. How to use this document

Feed it whole. When you implement a screen, re-read its section in §6 and the matching
rules in §4, §17 and §18 — most of the detail that makes this app trustworthy lives in
those rules, not in the happy path. If you find yourself about to show a number you
cannot attribute, stop: that is the one thing this app must never do.
