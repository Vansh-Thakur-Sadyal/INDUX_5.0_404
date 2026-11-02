# The farmer's record, their plots, and how a call-back actually happens

Added 15 Oct 2026. Three things that used to be assumed are now the farmer's to set:
who they are, where their fields are, and when Saarthi may ring them.

---

## Profile: typed details outrank anything heard on a call

`farmers` grew the columns a phone call could never establish reliably - full name,
age, tehsil, PIN code, landmark, years farming, farmer type, call delay. They are
written only from the dashboard, and `updated_by` records that.

The distinction matters because of a real incident: a call once heard the Hindi word
*bijai* (sowing) and stored it as the farmer's name, destroying "Ramesh Kumar". A
typed profile is authoritative; a call may fill a blank but not contradict it.

```
GET    /api/profile?phone=...        profile + fields + remembered facts + call settings
POST   /api/profile                  write what the farmer typed
GET    /api/profile/fields           their plots
POST   /api/profile/fields           create or update one plot
DELETE /api/profile/fields/{id}      remove a plot
DELETE /api/profile/memory           forget what the calls taught Saarthi
```

**A blank is not always a clear.** `language` is `NOT NULL`, so an empty form field
there means "leave it alone"; writing `NULL` raised `IntegrityError` and lost the whole
save, including the fields that *were* filled in (`NEVER_NULL` in `core/profile.py`).

**The placeholder identity is not an empty key.** Before a number is entered the
dashboard is `dashboard-owner`. `normalise_phone` keeps the last ten *digits*, so that
text reduces to `""` - and every farmer without a number would then share one row and
see each other's plots. `profile._key()` keeps the text when there are no digits.

The browser remembers the number in `localStorage` under `saarthi.phone` and sends it
on every profile and field request. Without that, the dashboard wrote to one row and
read back another, which looked exactly like "the profile did not save".

## Erasing memory

`DELETE /api/profile/memory?keep_name=true` deletes every remembered fact, every call
transcript and summary, and the sessions and turns behind them. It does **not** touch
the typed profile: "forget what you remember about me" is not "delete the address I
entered". The name is kept by default so calls still greet the farmer; `keep_name=false`
clears that too.

## Fields on the map

Leaflet 1.9.4 over standard OpenStreetMap tiles - no key, no account. (Carto's hosted
basemaps were tried first and served `MAP KEY REQUIRED` tiles.) Dark mode inverts the
tile pane in CSS rather than pulling in a second provider, so the plot rectangles drawn
on top keep their real colours.

A click drops a **fixed-size** plot box (`PLOT_HALF_SIZE`, about 65 m). That is
deliberate: a farmer on a phone cannot trace a field boundary accurately, and a box of
roughly the right size in the right place is more honest than a precise-looking polygon
nobody checked. The marker is draggable; the form takes size, crop, soil, irrigation and
sowing date.

If the CDN is blocked the map area shows a fallback and the latitude/longitude can be
typed instead - the page still works.

## Call-backs: one at a time, and the status is the truth

Three faults, all seen live:

1. A request was recorded as `requested` whether or not Twilio accepted it. The farmer
   pressed the button, the dashboard said a call was coming, and nothing rang.
2. The number went out as typed. Twilio needs E.164, so `7719605581` was rejected - and
   that rejection was the thing nobody saw.
3. Nothing stopped five presses becoming five calls.

Now: `to_e164()` normalises on every path that dials; `place_call` only reports success
when a call SID comes back, and stores the reason when one does not; and
`scheduler.pending_for()` refuses a second request while one is live.

The HTTP status says which kind of failure it was, because the dashboard has to tell
them apart:

| code | meaning |
|---|---|
| 400 | the number cannot be dialled - the farmer's to fix |
| 409 | a call to this number is already on its way |
| 502 | the request was fine; telephony refused it |

**The delay** is a profile setting (`call_delay_minutes`). A booked call is stored with
`dial_at` and dialled by a background sweeper, not an `asyncio` timer - a restart during
the wait would otherwise drop someone's call silently. A request that never reaches a
terminal status stops blocking the farmer after `STALE_AFTER_MINUTES`.

## One call log, not two

Conversational calls were written to `farmer.call_log` (the memory side) while the
dashboard read the `calls` table, so live calls never appeared in the log and outbound
requests sat on "requested" for ever. `/api/voice/turn` now mirrors into `calls`:
`storage.get_call` matches on `call_id` **or** `call_sid`, so when n8n sends Twilio's
SID the conversation attaches to the request that started it instead of creating a
second, disconnected record. `/api/voice/end` closes it with the reported status.

## Manual sensor values

`POST /api/sensors/manual` stores figures the farmer typed - from a soil-test slip or
their own meter - through the same validation path as everything else. Blank parameters
are filled from the current scenario, so a farmer with only a pH does not have to invent
the rest.

Provenance is `farmer_reported`, not `simulated`: the numbers did not come off a device,
but they are not generated either, and every trust decision downstream reads that field.
The reading becomes the latest one, so all 15 agents reason over it.


---

## One account, both channels (16 Oct 2026)

The voice agent used to be told six things about a caller: name, village, district,
land, soil, irrigation. A farmer who had filled in their whole profile was still asked
their village on the next call, and the plots they had drawn on the map were invisible
to it.

`farmers.memory_block()` now returns the **whole account** - every profile column the
farmer filled in, plus the fields they mapped - and `memory_prompt()` renders all of it,
ending with "Everything above is already known - do not ask for it again."

`mapped_fields()` reads the `fields` table with plain SQL rather than importing
`core.profile`, because that module imports `core.farmers`; the voice agent must not
depend on the dashboard's import order.

### The agents point at the farmer's farm

`apply_profile()` (module level in `voice_agent.py`, used by both the phone line and
the dashboard) fills the `FarmContext` the 15 agents reason over. Precedence, widest
to narrowest:

1. the typed profile - village, district, state, soil, irrigation, acreage, location
2. the first mapped plot - its crop, sowing date, soil and irrigation are more specific
   than a general profile
3. facts heard on a call - what the farmer said today beats what the map said last month

Before this, the dashboard answered about the demo farm while the farmer's own details
sat in the database unread.

### The dashboard is the same farmer as the phone

`/api/chat` takes a `phone`, and the browser sends the number saved on the profile
(`saarthi.phone` in localStorage). Keying the assistant on the session id instead gave
every browser tab its own memory, so nothing said on the dashboard was ever found again
- not on the next visit, and not by the phone line.

Verified live: a profile and a plot saved on the dashboard, then a call to the same
number greeted "नमस्ते Ramesh जी" and asked "aapke North plot ki mustard kaisi chal rahi
hai?" - the name from the profile, the plot and crop from the map.

## Dark mode is black

The first dark theme tinted every surface green and the whole screen read as green
rather than dark. The surfaces are now neutral (`--bg:#0b0b0d`) and the greens are
accents only - buttons, the active nav item, the charts.

There is **one** dark block in the stylesheet, not two. A small script in `<head>`
resolves the theme to an explicit `data-theme` before first paint, so the OS preference
needs no separate `@media` copy of the same twenty variables to drift out of step with.

## Maps

Both the overview card and the My Fields page carry a map, sharing one set of code
keyed by name (`maps`), so the zoom and full-screen buttons address either through a
data attribute. The overview map keeps the exact height of the illustration it replaced,
so the grid does not reflow.

Full screen tries the native Fullscreen API first and falls back to pinning the wrapper
over the viewport with CSS where that is refused - an embedded view, an iframe without
the permission. Escape leaves either. It is the wrapper that goes full screen, not the
map element: the zoom buttons are siblings of the map and would otherwise be left
behind on the page underneath.
