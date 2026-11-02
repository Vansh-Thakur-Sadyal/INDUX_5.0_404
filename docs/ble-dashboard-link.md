# Reading the HM-10 from the dashboard

The dashboard pairs with the HM-10 itself, over Web Bluetooth, and reads the same lines
the *Serial Bluetooth Terminal* app shows. Nothing else has to exist for a reading to
arrive: no Android companion app, no device token, no inbound port on the server.

Sensor Devices → **Live BLE Link** → *Connect device* → *Fetch values*.

---

## What the sketch should print

Labels are what matter. Every number is filed under the field whose label arrived next
to it, and a label this page does not recognise is listed and dropped rather than
guessed at. There is deliberately no positional fallback: a sketch that printed N, P, K
in a different order than assumed would otherwise file phosphorus as nitrogen, and a
wrong nutrient figure sends a farmer to buy the wrong bag of fertiliser.

All of these work:

```
N: 45 mg/kg                         one per line
P: 20 mg/kg
K: 180 mg/kg
Temp: 24.5 C
Moisture: 42.0 %
EC: 800 uS/cm
pH: 6.80
```

```
N: 45 P: 20 K: 180 T: 24.5 M: 42 EC: 0.85 PH: 6.8      all on one line
N=45,P=20,K=180                                         = and commas are fine
N 45  P 20  K 180                                       spaces alone also work
Nitrogen (N): 45 mg/kg                                  long names, brackets
{"nitrogen_mg_kg":45,"ph":6.8}                          JSON
{"registers":[420,241,800,68,32,18,210]}                the raw Modbus block
```

### Accepted labels

| Field | Labels accepted (case does not matter) |
|---|---|
| Nitrogen | `N`, `nitrogen`, `nitro`, `nitrogen_mg_kg` |
| Phosphorus | `P`, `phosphorus`, `phos`, `phosphorous`, `phosphorus_mg_kg` |
| Potassium | `K`, `potassium`, `potash`, `potassium_mg_kg` |
| Soil temperature | `T`, `temp`, `temperature`, `soil temp`, `temperature_c` |
| Soil moisture | `M`, `moist`, `moisture`, `soil moisture`, `VWC`, `humidity`, `hum` |
| EC | `EC`, `conductivity`, `salinity`, `ec_ds_m` |
| pH | `pH`, `soil pH`, `acidity` |
| Position | `LAT`, `LON`/`LNG`, `SATS`/`satellites`, `SPD`/`speed`, `ALT`, `FIX` |

`P` is phosphorus and `PH` is pH — they are matched exactly, not by prefix.

`humidity` maps to **soil moisture**, because the register a 7-in-1 probe reports
volumetric water content on is the one many sketches label humidity. Whatever the
label, the page prints *what each incoming key became* for every fetch, so a mapping
you disagree with is visible rather than silent.

### Units

`EC` is converted when the unit says so: `800 uS/cm` is stored as `0.8 dS/m`. Without a
unit the number is taken as-is, and 800 dS/m then fails the plausibility check and is
dropped rather than used. Temperature in `F` is converted to `C`.

Units are only read in the labelled form (`N: 45 mg/kg`). In the spaces-only form
(`N 45 mg P 20`) there is no way to tell a unit from the next label, so none is read.

### Packet boundaries

A packet is finished when any of these happens: all seven values have arrived, a
separator line (`-----`, `END`) is printed, a field repeats (the next cycle has begun),
or the line falls quiet for 700 ms. The sketch sending one burst every 20 s needs
nothing extra; a trailing `-----` just makes it immediate.

### The GPS

The position is kept as running state, not as part of a soil packet. The OLED
alternates its soil and GPS screens every five seconds while the values refresh every
twenty, so the two blocks are not one packet, and a GPS block that shared a frame with
nothing else would otherwise be parsed and thrown away.

`FIX: 0` is honoured: coordinates arriving without a fix are shown but refused as a
sensor location, because they are the last known or default position and would put the
probe in the wrong field.

---

## Provenance

The **Live BLE Link** card has a checkbox, *A real soil probe is wired in*, which is
off by default and decides how the reading is labelled:

| | Stored as | Means |
|---|---|---|
| unticked | `DEVICE_SIMULATED` | The packet really arrived over BLE, but the Arduino generated the seven numbers itself. Not measurements. |
| ticked | `REAL_SENSOR` | Measured by the probe in the ground. |

This exists because at the current build stage the values are generated on the Arduino.
The link is real and the packet is real; the numbers are not, and the label has to say
so. The provenance travels with the reading into the soil page, the reports, the
agents and the voice agent.

A value outside its plausible range is **dropped, never clamped** — a clamped number
looks like a measurement. Parameters that were not in the packet read as *no value*,
and nothing is filled in for them.

---

## Browser support

Web Bluetooth needs **Chrome or Edge** on Android, Windows, macOS or Linux, over HTTPS
(`https://saarthi2.0.visioncoders.in`) or `http://localhost` while developing.

Safari and iOS do not support it, and neither does Firefox. The page says which of
these applies rather than failing at the device chooser.

Only one device can hold the link at a time, so close the Serial Bluetooth Terminal app
before connecting here.

## Testing without the hardware

*Test the parser without the hardware* on the same card takes pasted lines through the
identical path — buffered, split on newlines, parsed, framed, fetched and stored.

The parser has its own tests, which need no browser:

```bash
node frontend/ble-parse.test.js
```
