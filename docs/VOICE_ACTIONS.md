# Telling Saarthi to do something

Until now a spoken turn could only produce words. Now a sentence can be an
instruction: *"mandi bhav dikhao"* opens the market page, *"poori jaanch karo"*
runs the chain, *"band karo"* stops it. And the phone can be woken by name.

---

## 1. The rule everything here protects

**A question must never become an action.**

| Said | What happens |
|---|---|
| "gehu ka bhav **kya hai**" | a question — the agents answer it with a real price |
| "gehu ka bhav **dikhao**" | an instruction — the market page opens |

Getting that wrong one way means the screen jumps while the farmer is reading;
the other way means the feature does not work. The first is worse.

So **detection is by rule, never by a model**. Every match is a word the farmer
actually said, which means a wrong match can be explained and fixed. A model's
guess cannot, and would decide differently on the next identical sentence.

It is also why an instruction is answered in **4–19 ms** while a question takes
seconds: nothing is generated. The words are fixed.

---

## 2. What it can be told to do

From `/api/voice/actions`, which is also what the dashboard's "you can also say"
chips are built from — so the help cannot drift from the phrases that work.

| Action | Risk | Say |
|---|---|---|
| Open a page | read | "mandi bhav dikhao", "open my fields", "mitti wala page kholo" |
| Run the full check | read | "poori jaanch karo", "run the full check" |
| Take a soil reading | read | "sensor se reading lo", "take a new reading" |
| Open reports | read | "report banao", "make me a report" |
| Stop | read | "band karo", "stop" |
| Record money | write | "do hazaar ka urea liya" |
| Book a call-back or a watch | write | "shaam ko paanch baje call karna" |

Three risk levels, and they are the whole safety story:

- **read** — answer or show something. Run it.
- **write** — change the farmer's own data. Run it, and say exactly what changed.
- **confirm** — costs money, rings a phone, or cannot be undone. Ask first, in
  one sentence, and act only on a clear yes. "theek hai chhodo" is a no.

**Fourteen pages are reachable by voice, and the admin console and the knowledge
base are not in that list.** "open admin" from a farmer's phone is a sentence
Saarthi does not understand, rather than a redirect it refuses.

---

## 3. How an instruction reaches a screen

The server cannot open a page. It can only say that one should be opened.

```
    the surface that was asked          a phone call
    ──────────────────────────          ─────────────
    reply carries the instruction       nothing to reply to
    the page acts at once (216 ms)      → queued per account
                                        → the dashboard or the app
                                          collects it (1.5–2.5 s)
```

The surface that was asked does **not** also get a queued copy. It used to, and
the dashboard navigated inline and then collected its own copy a second later
and navigated again — so a question asked straight after an instruction was
dragged back to the previous page.

The queue has three rules, each from a way this goes wrong:

- **delivered once**, so two open tabs do not both navigate;
- **expires after 150 s** — "open the sensor page" arriving an hour after the
  call is not help, it is a haunted browser;
- **scoped to the account**, on the same phone-number key the rest of the system
  uses.

Only the instructions this server knows how to produce are accepted:
`navigate`, `run_pipeline`, `fetch_reading`, `make_report`, `stop`, and
`navigate` only to a page in the list. A browser is going to act on whatever
comes back, so it is validated on the way out as well as on the way in.

---

## 3b. On a phone call

A phone call is not a screen, and the same sentence means different things down
a line. Before this, a caller got:

| Said | Got | Why that was wrong |
|---|---|---|
| "sensor se reading lo" | *"Opening the sensor page..."* | In English, to a Hindi caller, holding a phone with no page to open |
| "mujhe report bhej do" | *"Ji, report toh main bhej doonga."* | **It promised to send something it cannot send** |
| "dobara boliye" | a brand new sentence | The one thing that does not help somebody who missed the first one |
| "kisi aadmi se baat karani hai" | *"I can connect you. Which person?"* | It cannot connect anyone |

Every handler is now told its **channel** and the farmer's **language**.

| Said on a call | What happens |
|---|---|
| "mandi bhav dikhao" | Opens it on their dashboard, and **says so** — not "opening", which implies they can see it |
| "sensor se reading lo" | *"The probe talks to the app on your phone, not to me on this call."* |
| "report bhej do" | *"I cannot send a report from this call. It is on your dashboard."* |
| "poori jaanch karo" | **Actually starts the chain** and says to ask again in a minute |
| "dobara boliye" | Repeats the **same words**, read back from the transcript |
| "english mein baat karo" | Switches the call, confirms **in the new language**, and it sticks |
| "helpline number batao" | The real Kisan Call Centre number, 1800 180 1551 |

Three of those exist only on a call — `repeat`, `set_language`, `helpline` —
because a call cannot be scrolled back, re-read, or switched with a dropdown.

**Language.** The replies are written out in English and Hindi and fall back to
English for the other eleven. Not because the others do not matter, but because
a confident sentence in a language nobody here can check is worse than a plain
one the farmer can puzzle through — this is text that goes straight into
somebody's ear with no chance to re-read it.

The Hindi is Hinglish in Latin script, which is what the TTS voice reads most
naturally and what the rest of the spoken replies already use.

**The helpline number is the national Kisan Call Centre, not this deployment's
own line.** That one is a demo number and the wrong thing to hand a farmer who
needs help.

**Measured: 16 ms end to end over HTTP**, because nothing is generated.

---

## 4. "Saarthi" as a wake word

### In the browser

Chrome's speech recognition, listening continuously for the name.

- **Off until switched on**, with the switch remembered per browser.
- A **red dot** while the microphone is live, visible from across a room. The
  colour is literal rather than a theme token so it cannot be styled away.
- **Stops when the tab is hidden.** A page listening to a room nobody is looking
  at is not a feature.
- Firefox and most in-app browsers have no `SpeechRecognition`; the control is
  hidden rather than appearing broken.

On desktop Chrome the audio goes to Google to be transcribed. That is a real
privacy cost and it is the farmer's to accept, which is why it is opt-in.

Say **"Saarthi"** on its own and the assistant opens listening. Say
**"Saarthi, mandi bhav dikhao"** and the whole sentence is acted on — a farmer
says the name and the request in one breath, and making them wait for a beep is
how a voice feature stops being used.

### On the phone, including locked

A foreground service holding the microphone, running Android's `SpeechRecognizer`
in a restart loop. Waking launches the app with `showWhenLocked` and
`turnScreenOn`.

**What it honestly is not:** a match for the assistant built into the phone.
Those use a dedicated low-power wake-word chip. This drives the general
recogniser, which means it uses noticeably more battery than an idle phone,
Android may stop it under Doze after a long screen-off, and a very noisy field
will miss wakes. Those costs are stated on the toggle rather than discovered.

**On a secure lock screen** Android shows the activity but keeps the device
locked until the farmer unlocks it. No app may override that. So being woken
lights the screen and gets Saarthi listening; anything beyond that still needs
the PIN. That is the platform's decision and the only behaviour available.

The detector sits behind an interface, so a dedicated engine (Porcupine,
openWakeWord) can replace it without touching the service, the notification or
the lock-screen handling.

**The notification cannot be dismissed while it runs, and stops it in one tap.**
A microphone a person cannot switch off from where they see it is on is not one
they will leave on.

### Hearing the name

Generous about spelling, strict about word boundaries:

| Heard | Wakes? |
|---|---|
| saarthi, sarthi, sarathi, saathi, hey saarthi, सारथी | yes |
| **parathi**, **marathi**, "my parathi is cold" | no |

A missed wake is annoying and the farmer repeats themselves. A false wake lights
the phone up mid-conversation, which is what makes people turn the feature off
for good.

---

## 5. API

| Method | Path | Does |
|---|---|---|
| `GET` | `/api/voice/actions` | Everything that can be asked for out loud, with examples |
| `GET` | `/api/voice/commands` | Instructions waiting for this screen; delivered once |
| `POST` | `/api/voice/command` | Queue one for this account's other screens |
| `POST` | `/api/voice/commands/clear` | Drop whatever is waiting |

All four need a signed-in account. They are deliberately **not** reachable with
the shared telephony token: that token belongs to n8n, which has no business
opening a farmer's dashboard.

`POST /api/chat` and `/api/chat/stream` carry `action` and `ui` when the message
was an instruction, and no advisory — the chain is not run.

---

## 6. Where the code is

| | |
|---|---|
| `backend/app/conversation/actions.py` | the registry, the parser, the risk levels |
| `backend/app/core/commands.py` | the queue, its expiry and its scoping |
| `backend/app/routers/voice.py` | the four endpoints |
| `backend/app/routers/chat.py` | `_instruction()` — one check, every text channel |
| `frontend/wake.js` | browser wake word, the poller, the chips |
| `App/.../core/speech/WakeWord.kt` | hearing the name (tested on its own) |
| `App/.../core/speech/WakeWordService.kt` | the foreground service |
| `App/.../core/VoiceBus.kt` | wake and commands, on their way to a screen |

Tested in `backend/tests/test_voice_actions.py` (57),
`backend/tests/test_call_actions.py` (31) and `App/.../WakeWordTest.kt` (10).

One of those tests exists because of a bug it caught: *"phir se baat kar raha
hoon"* — "I am speaking to you again", a greeting — was being answered by
repeating the previous call's last sentence. The word for *again* is no longer
enough on its own; it has to arrive with a word about **saying**.
