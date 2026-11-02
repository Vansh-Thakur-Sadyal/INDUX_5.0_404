/* Tests for the HM-10 line parser. Run with: node frontend/ble-parse.test.js
   No framework - node is already on the machine and this file has no dependencies.

   The cases are the ones that would produce a WRONG number on the dashboard rather
   than a missing one, because a missing value shows as "no value" and a wrong value
   shows as agronomic advice. */
"use strict";

const assert = require("assert");
const B = require("./ble-parse.js");

let passed = 0;
const tests = [];
function test(name, fn) { tests.push([name, fn]); }

/* ------------------------------------------------- the labelled formats */
test("a labelled N P K line lands each nutrient in its own field", () => {
  const out = B.parseLine("N: 45 mg/kg  P: 20 mg/kg  K: 180 mg/kg");
  assert.deepStrictEqual(out.values, {
    nitrogen_mg_kg: 45, phosphorus_mg_kg: 20, potassium_mg_kg: 180,
  });
  assert.strictEqual(out.unknown.length, 0);
});

test("P is phosphorus and pH is pH, which a looser match would confuse", () => {
  const out = B.parseLine("P=20, PH=6.8");
  assert.strictEqual(out.values.phosphorus_mg_kg, 20);
  assert.strictEqual(out.values.ph, 6.8);
});

test("labels with no units on one line keep every value", () => {
  /* This is the case that caught a real bug: the unit group matched the next
     label, so "P" became the unit of 45 and phosphorus never reached the page. */
  const out = B.parseLine("N: 45 P: 20 K: 180 T: 24.5 M: 42 EC: 0.85 PH: 6.8");
  assert.strictEqual(Object.keys(out.values).length, 7, JSON.stringify(out.values));
  assert.strictEqual(out.values.phosphorus_mg_kg, 20);
});

test("a coordinate keeps six decimal places", () => {
  /* Rounded to three, 27.1767 becomes 27.177 - about 100 m away, which is a
     different corner of the plot. */
  assert.strictEqual(B.parseLine("LAT: 27.176700").gps.latitude, 27.1767);
});

test("one value per line, the way a sketch usually prints it", () => {
  const lines = ["Nitrogen (N): 45 mg/kg", "Phosphorus (P): 20 mg/kg",
                 "Potassium (K): 180 mg/kg", "Soil Temperature: 24.5 C",
                 "Soil Moisture: 42.0 %", "EC: 0.85 dS/m", "pH: 6.80"];
  const frame = new B.Frame();
  lines.forEach((line, i) => frame.feed(line, 1000 + i * 20));
  assert.deepStrictEqual(frame.snapshot().values, {
    nitrogen_mg_kg: 45, phosphorus_mg_kg: 20, potassium_mg_kg: 180,
    temperature_c: 24.5, moisture_pct: 42, ec_ds_m: 0.85, ph: 6.8,
  });
  assert.strictEqual(frame.snapshot().missing.length, 0);
});

test("a JSON line is taken as it is", () => {
  const out = B.parseLine('{"N":45,"P":20,"K":180,"temp":24.5,"moisture":42,"ec":0.85,"ph":6.8}');
  assert.strictEqual(Object.keys(out.values).length, 7);
  assert.strictEqual(out.values.temperature_c, 24.5);
});

test("space-separated labels work when the sketch prints no colons", () => {
  const out = B.parseLine("N 45  P 20  K 180");
  assert.deepStrictEqual(out.values, {
    nitrogen_mg_kg: 45, phosphorus_mg_kg: 20, potassium_mg_kg: 180,
  });
});

test("the line the documented sketch sends parses with nothing left over", () => {
  /* Copied from the output shape of docs/arduino-hm10-sketch.ino. */
  const line = '{"device_id":"7IN1-UNO-01","parcel_id":"field-1","mode":"real",'
    + '"ok":true,"moisture_pct":42.0,"temperature_c":24.1,"ec_ds_m":0.800,'
    + '"ph":6.8,"nitrogen_mg_kg":32,"phosphorus_mg_kg":18,"potassium_mg_kg":210,'
    + '"registers":[420,241,800,68,32,18,210]}';
  const out = B.parseLine(line);
  assert.strictEqual(out.values.moisture_pct, 42);
  assert.strictEqual(out.values.potassium_mg_kg, 210);
  assert.strictEqual(Object.keys(out.values).length, 7);
  assert.deepStrictEqual(out.unknown, [],
    "device_id and mode are about the packet, not the soil, and must not be flagged");
});

test("a raw register block alone still reaches the page", () => {
  const out = B.parseLine('{"device_id":"7IN1-UNO-01","registers":[420,241,800,68,32,18,210]}');
  assert.strictEqual(out.values.moisture_pct, 42);      /* 420 x 0.1 */
  assert.strictEqual(out.values.temperature_c, 24.1);   /* 241 x 0.1 */
  assert.strictEqual(out.values.ec_ds_m, 0.8);          /* 800 x 0.001, uS/cm -> dS/m */
  assert.strictEqual(out.values.ph, 6.8);               /* 68 x 0.1 */
  assert.strictEqual(out.values.nitrogen_mg_kg, 32);
  assert.ok(out.mapping.every((m) => /provisional register map/.test(m.note)));
});

test("a named value beats one decoded from the provisional register map", () => {
  /* The map is still marked provisional, so an explicit figure must win. */
  const out = B.parseLine('{"ph":7.4,"registers":[420,241,800,68,32,18,210]}');
  assert.strictEqual(out.values.ph, 7.4);
});

/* --------------------------------- the format this farm's sketch actually sends */
test("the real packet from the farm sketch parses completely", () => {
  /* Copied byte for byte out of raw_packet on the live server. EC_US_CM resolved to
     nothing, so EC was dropped on every fetch - which is why EC read wrong and why
     seven values arrived as "6 of 7 (partial)". */
  const line = "N=28,P=63,K=91,TEMP_C=31.2,MOISTURE_PCT=59,EC_US_CM=1903,PH=7.2,GPS_FIX=NO";
  const out = B.parseLine(line);
  assert.deepStrictEqual(out.values, {
    nitrogen_mg_kg: 28, phosphorus_mg_kg: 63, potassium_mg_kg: 91,
    temperature_c: 31.2, moisture_pct: 59, ec_ds_m: 1.903, ph: 7.2,
  });
  assert.deepStrictEqual(out.unknown, [], "nothing in this packet should be unrecognised");
  assert.strictEqual(Object.keys(out.values).length, 7, "this is a full packet, not partial");
});

test("a unit carried in the label is read as the unit", () => {
  /* EC_US_CM is one token: the unit is part of the name, not a separate word. */
  const out = B.parseLine("EC_US_CM=1903");
  assert.strictEqual(out.values.ec_ds_m, 1.903);
  assert.match(out.mapping[0].note, /converted/);
});

test("other unit suffixes resolve the same way", () => {
  assert.strictEqual(B.parseLine("TEMP_C=31.2").values.temperature_c, 31.2);
  assert.strictEqual(B.parseLine("MOISTURE_PCT=59").values.moisture_pct, 59);
  assert.strictEqual(B.parseLine("N_MG_KG=28").values.nitrogen_mg_kg, 28);
  assert.strictEqual(B.parseLine("EC_DS_M=1.9").values.ec_ds_m, 1.9);
});

test("stripping a suffix never invents a field", () => {
  /* The suffix walk must not turn an unknown label into a known one. */
  const out = B.parseLine("BATTERY_MV=4120, XYZ_PCT=7");
  assert.deepStrictEqual(out.values, {});
});

test("an EC with no unit anywhere is read by magnitude, and says so", () => {
  /* 1903 dS/m does not occur in a field a crop grows in; 1903 uS/cm is ordinary.
     The number is kept and the assumption is written down rather than hidden. */
  const out = B.parseLine("EC=1903");
  assert.strictEqual(out.values.ec_ds_m, 1.903);
  assert.match(out.mapping[0].note, /no unit sent/);
});

test("a plausible dS/m value is left exactly alone", () => {
  assert.strictEqual(B.parseLine("EC=1.9").values.ec_ds_m, 1.9);
  assert.strictEqual(B.parseLine("EC=1.9").mapping[0].note, "");
});

test("GPS_FIX=NO is reported rather than ignored", () => {
  /* The value is a word, so the numeric scan never sees it. Knowing there is no fix
     is the difference between an empty location panel and one that explains itself. */
  assert.strictEqual(B.parseLine("GPS_FIX=NO").gps.gps_fix, false);
  assert.strictEqual(B.parseLine("GPS_FIX=YES").gps.gps_fix, true);
  assert.strictEqual(B.parseLine("N=28,GPS_FIX=NO").gps.gps_fix, false);
});

test("a line carrying only the fix flag is not reported as unparsed", () => {
  const out = B.parseLine("GPS_FIX=NO");
  assert.deepStrictEqual(out.unknown, []);
});

/* -------------------------------------------- what must NOT be guessed */
test("a line of bare numbers is never mapped by position", () => {
  const out = B.parseLine("45,20,180,24.5,42.0,0.85,6.8");
  assert.deepStrictEqual(out.values, {},
    "seven bare numbers must not be assumed to be N,P,K,T,M,EC,pH");
});

test("an unrecognised label is reported, not quietly dropped into a field", () => {
  const out = B.parseLine("Xyz: 99  Nitrogen: 45");
  assert.strictEqual(out.values.nitrogen_mg_kg, 45);
  assert.strictEqual(Object.keys(out.values).length, 1);
  assert.strictEqual(out.unknown.length, 1);
  assert.strictEqual(out.unknown[0].key, "Xyz");
});

test("a non-numeric value is refused rather than becoming NaN on the dashboard", () => {
  const out = B.parseLine('{"N":"--","P":20}');
  assert.strictEqual("nitrogen_mg_kg" in out.values, false);
  assert.strictEqual(out.values.phosphorus_mg_kg, 20);
  assert.strictEqual(out.unknown.length, 1);
});

test("positional parsing happens only when the order is passed in explicitly", () => {
  const order = ["nitrogen_mg_kg", "phosphorus_mg_kg", "potassium_mg_kg",
                 "temperature_c", "moisture_pct", "ec_ds_m", "ph"];
  const out = B.parseOrdered("45,20,180,24.5,42.0,0.85,6.8", order);
  assert.strictEqual(out.values.potassium_mg_kg, 180);
  assert.strictEqual(out.values.ph, 6.8);
  /* and it says so on every value it produced */
  assert.ok(out.mapping.every((m) => /order you chose/.test(m.note)));
});

test("ordered parsing ignores a line that is not pure numbers", () => {
  const out = B.parseOrdered("N: 45 mg/kg", ["nitrogen_mg_kg"]);
  assert.deepStrictEqual(out.values, {});
});

/* ------------------------------------------------------------ the units */
test("EC in uS/cm is converted, because 800 dS/m would be thrown out as impossible", () => {
  const out = B.parseLine("EC: 800 uS/cm");
  assert.strictEqual(out.values.ec_ds_m, 0.8);
  assert.match(out.mapping[0].note, /converted/);
});

test("EC already in dS/m is left alone", () => {
  assert.strictEqual(B.parseLine("EC: 0.85 dS/m").values.ec_ds_m, 0.85);
});

test("every mapping says which incoming key became which field", () => {
  const out = B.parseLine("Hum: 42 %");
  assert.deepStrictEqual(
    out.mapping.map((m) => [m.key, m.field]), [["hum", "moisture_pct"]]);
});

/* -------------------------------------------------------------- the GPS */
test("the GPS block is read into its own fields, not into soil values", () => {
  const out = B.parseLine("LAT: 27.176700 LON: 78.008100 SATS: 7 SPD: 0.0");
  assert.deepStrictEqual(out.values, {});
  assert.strictEqual(out.gps.latitude, 27.1767);
  assert.strictEqual(out.gps.longitude, 78.0081);
  assert.strictEqual(out.gps.gps_satellites, 7);
});

test("a fix flag becomes a boolean", () => {
  assert.strictEqual(B.parseLine("FIX: 1").gps.gps_fix, true);
  assert.strictEqual(B.parseLine("FIX: 0").gps.gps_fix, false);
});

test("satellite count is a whole number", () => {
  assert.strictEqual(B.parseLine("Satellites: 7.0").gps.gps_satellites, 7);
});

/* ----------------------------------------------------------- the frames */
test("a frame is not settled while lines are still arriving", () => {
  const frame = new B.Frame();
  frame.feed("N: 45", 1000);
  assert.strictEqual(frame.settled(1100, 700), false, "100 ms of quiet is not the end");
  assert.strictEqual(frame.settled(1800, 700), true);
});

test("an empty frame never settles, so fetch cannot capture nothing", () => {
  const frame = new B.Frame();
  frame.feed("Saarthi soil node starting", 1000);
  assert.strictEqual(frame.settled(9000, 700), false);
});

test("all seven values complete a frame without waiting for the gap", () => {
  const frame = new B.Frame();
  frame.feed("N:45 P:20 K:180 T:24.5 M:42 EC:0.85 PH:6.8", 1000);
  assert.strictEqual(frame.complete, true);
  assert.strictEqual(frame.settled(1000, 700), true);
});

test("a repeated field means the next 20 s cycle began, so the frame closes", () => {
  const frame = new B.Frame();
  frame.feed("N: 45", 1000);
  frame.feed("P: 20", 1020);
  const again = frame.feed("N: 46", 21000);
  assert.strictEqual(again.restarted, true);
  assert.strictEqual(frame.complete, true);
  /* the frame keeps the first cycle intact; 46 belongs to the next one */
  assert.strictEqual(frame.snapshot().values.nitrogen_mg_kg, 45);
});

test("a terminator line closes the frame", () => {
  const frame = new B.Frame();
  frame.feed("N: 45", 1000);
  frame.feed("-----", 1010);
  assert.strictEqual(frame.complete, true);
});

test("a frame carrying only nitrogen reports the other six as missing", () => {
  const frame = new B.Frame();
  frame.feed("N: 45", 1000);
  const snap = frame.snapshot();
  assert.deepStrictEqual(snap.received, ["nitrogen_mg_kg"]);
  assert.strictEqual(snap.missing.length, 6);
  assert.ok(snap.missing.includes("ph"));
});

test("the raw lines are kept so the stream can be shown as it arrived", () => {
  const frame = new B.Frame();
  frame.feed("N: 45 mg/kg", 1000);
  frame.feed("P: 20 mg/kg", 1010);
  assert.strictEqual(frame.snapshot().raw, "N: 45 mg/kg\nP: 20 mg/kg");
});

/* ------------------------------------------------------------- the run */
for (const [name, fn] of tests) {
  try {
    fn();
    passed += 1;
  } catch (error) {
    console.error("FAIL  " + name + "\n      " + error.message);
    process.exitCode = 1;
  }
}
console.log(passed + "/" + tests.length + " parser tests passed");
