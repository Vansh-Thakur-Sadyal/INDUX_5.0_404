/* Parsing what the HM-10 sends, with no guessing.

   The Arduino prints seven soil values plus a GPS block over a software serial link
   into the HM-10, which this file turns back into named fields. It is pure - no
   Bluetooth, no DOM, no timers - so the same code runs in the browser and under node
   in the test suite.

   The one rule everything here is built around: a number is only ever stored under a
   field whose LABEL arrived with it. There is no positional fallback, because a sketch
   that prints N, P, K in a different order than we assumed would silently file
   phosphorus as nitrogen, and a wrong nutrient figure sends a farmer to buy the wrong
   bag of fertiliser. A label we do not recognise is reported as unknown and dropped.
   Bare comma-separated numbers are parsed only when the operator explicitly picks that
   format and states the order themselves. */
(function (global) {
  "use strict";

  /* Canonical field names, matching backend/app/sensors/validation.py PARAMETERS. */
  var FIELDS = ["nitrogen_mg_kg", "phosphorus_mg_kg", "potassium_mg_kg",
                "temperature_c", "moisture_pct", "ec_ds_m", "ph"];

  /* Every spelling we accept for each field. Keys here are already normalised.
     Note "humidity": a 7-in-1 soil probe reports volumetric water content on the
     register many sketches label humidity, so it maps to soil moisture - and the UI
     prints the mapping it used for every incoming key, so this is never invisible. */
  var FIELD_ALIASES = {
    n: "nitrogen_mg_kg", nitrogen: "nitrogen_mg_kg", nitro: "nitrogen_mg_kg",
    p: "phosphorus_mg_kg", phosphorus: "phosphorus_mg_kg", phos: "phosphorus_mg_kg",
    phosphorous: "phosphorus_mg_kg",
    k: "potassium_mg_kg", potassium: "potassium_mg_kg", potash: "potassium_mg_kg",
    t: "temperature_c", temp: "temperature_c", temperature: "temperature_c",
    soiltemp: "temperature_c", soiltemperature: "temperature_c", tempc: "temperature_c",
    m: "moisture_pct", moist: "moisture_pct", moisture: "moisture_pct",
    soilmoisture: "moisture_pct", vwc: "moisture_pct", water: "moisture_pct",
    humidity: "moisture_pct", hum: "moisture_pct",
    ec: "ec_ds_m", conductivity: "ec_ds_m", ecvalue: "ec_ds_m",
    electricalconductivity: "ec_ds_m", salinity: "ec_ds_m",
    ph: "ph", phvalue: "ph", soilph: "ph", acidity: "ph"
  };

  /* The canonical names are aliases of themselves. A sketch that already prints the
     field names the backend uses - as docs/arduino-hm10-sketch.ino does - was
     otherwise rejected by the very table meant to accept it. */
  FIELDS.forEach(function (field) {
    FIELD_ALIASES[normaliseKey(field)] = field;
  });

  var GPS_ALIASES = {
    lat: "latitude", latitude: "latitude",
    lon: "longitude", lng: "longitude", long: "longitude", longitude: "longitude",
    sats: "gps_satellites", sat: "gps_satellites", satellites: "gps_satellites",
    satellite: "gps_satellites", sv: "gps_satellites",
    spd: "gps_speed_kmph", speed: "gps_speed_kmph",
    alt: "gps_altitude_m", altitude: "gps_altitude_m",
    hdop: "gps_hdop"
  };

  /* Keys that are about the packet rather than the soil. They are skipped in silence:
     listing device_id and mode as unrecognised would bury a genuinely unknown label
     in noise, which is the one thing the unknown list exists to surface. */
  var IGNORE_KEYS = {
    deviceid: 1, parcelid: 1, mode: 1, ok: 1, error: 1, provenance: 1,
    recordedat: 1, receivedat: 1, seed: 1, scenario: 1, id: 1, seq: 1, count: 1,
    battery: 1, rssi: 1, uptime: 1, status: 1
  };

  /* The provisional Modbus map, in register order, matching REGISTER_MAP in
     backend/app/sensors/validation.py. The documented sketch sends the raw block
     alongside the decoded values, so a packet that carries only the block still
     reaches the page. Scales are the same ones the backend applies. */
  var REGISTER_ORDER = [
    { field: "moisture_pct", scale: 0.1 },
    { field: "temperature_c", scale: 0.1 },
    { field: "ec_ds_m", scale: 0.001 },
    { field: "ph", scale: 0.1 },
    { field: "nitrogen_mg_kg", scale: 1 },
    { field: "phosphorus_mg_kg", scale: 1 },
    { field: "potassium_mg_kg", scale: 1 }
  ];

  /* Lines that mean "that was the whole frame", so the next cycle starts clean. */
  var TERMINATORS = /^[\s\-=*_>#.]+$|^(end|eof|done)$/i;

  function normaliseKey(raw) {
    var text = String(raw == null ? "" : raw).toLowerCase();
    var paren = text.indexOf("(");
    if (paren > 0) text = text.slice(0, paren);        /* "nitrogen (n)" -> "nitrogen" */
    return text.replace(/[^a-z0-9]/g, "");
  }

  /* Resolve a label to a field, trying the whole label and then its individual words.
     The whole label first, so "soil moisture" wins as a unit; the last word next, which
     is what rescues a key the regex over-captured ("...mg/kg P" -> "p"); the first word
     last, for "temp sensor". Every candidate still has to be in the alias table. */
  function resolveKey(raw, table) {
    var whole = normaliseKey(raw);
    if (table[whole]) return { key: whole, field: table[whole] };

    /* The unit may be part of the label rather than a separate token: EC_US_CM,
       TEMP_C, N_MG_KG. Drop trailing pieces one at a time until something resolves,
       and hand back what was dropped so it can be used as the unit. Without this the
       whole reading is discarded for the sake of a suffix. */
    var text = String(raw == null ? "" : raw).trim().toLowerCase();
    var parts = text.split(/[^a-z0-9]+/).filter(Boolean);
    for (var cut = parts.length - 1; cut >= 1; cut--) {
      var head = normaliseKey(parts.slice(0, cut).join(""));
      if (table[head]) {
        return { key: head, field: table[head],
                 unitFromLabel: parts.slice(cut).join("/") };
      }
    }

    var words = String(raw == null ? "" : raw).trim().split(/[\s,;|]+/).filter(Boolean);
    var order = [];
    if (words.length > 1) order.push(words[words.length - 1], words[0]);
    for (var i = 0; i < order.length; i++) {
      var candidate = normaliseKey(order[i]);
      if (table[candidate]) return { key: candidate, field: table[candidate] };
    }
    return null;
  }

  /* EC comes off these probes in either dS/m or uS/cm, a factor of 1000 apart. A
     uS/cm figure stored as dS/m fails the plausibility check rather than being used,
     but converting on the stated unit is better than losing the reading. */
  function convert(field, value, unit) {
    var u = String(unit || "").toLowerCase().replace(/[^a-z%\/]/g, "");
    if (field === "ec_ds_m" && (u === "us/cm" || u === "uscm" || u === "s/cm" || u === "scm")) {
      return { value: value / 1000, note: "converted from " + unit + " to dS/m" };
    }
    if (field === "ec_ds_m" && (u === "ms/cm" || u === "mscm")) {
      return { value: value, note: "mS/cm read as dS/m (same magnitude)" };
    }
    /* No unit given anywhere and the number is impossible as dS/m. Soil EC above
       20 dS/m does not occur in a field a crop is growing in, while 1903 is an
       ordinary uS/cm reading, so the unit is the thing that is missing - not the
       sensor. Stored as dS/m with the assumption written into the mapping table. */
    if (field === "ec_ds_m" && !u && value > 20) {
      return { value: value / 1000,
               note: "no unit sent; " + value + " is out of range for dS/m, read as uS/cm" };
    }
    if (field === "temperature_c" && (u === "f" || u === "degf")) {
      return { value: (value - 32) * 5 / 9, note: "converted from F to C" };
    }
    return { value: value, note: "" };
  }

  function round(value) {
    return Math.round(value * 1000) / 1000;
  }

  /* Coordinates need six places. Rounding a latitude to three, as a soil value is
     rounded, moves the point by about 100 m - far enough to land in the next plot. */
  function roundCoord(value) {
    return Math.round(value * 1e6) / 1e6;
  }

  function take(out, rawKey, rawValue, unit, line) {
    var value = typeof rawValue === "number" ? rawValue : parseFloat(rawValue);
    var soil = resolveKey(rawKey, FIELD_ALIASES);
    var gps = soil ? null : resolveKey(rawKey, GPS_ALIASES);

    if (!soil && !gps) {
      if (IGNORE_KEYS[normaliseKey(rawKey)]) return;
      /* A fix flag is a word rather than a number, so it is handled on its own. */
      if (/(^|[^a-z])fix$/.test(normaliseKey(rawKey)) || normaliseKey(rawKey) === "gpsfix"
          || normaliseKey(rawKey) === "fix") {
        out.gps.gps_fix = /^(1|true|yes|valid|ok|fix)$/i.test(String(rawValue).trim());
        out.mapping.push({ key: "fix", field: "gps_fix", value: out.gps.gps_fix, note: "" });
        return;
      }
      out.unknown.push({ key: String(rawKey).trim(), value: rawValue, line: line });
      return;
    }
    if (!isFinite(value)) {
      out.unknown.push({ key: String(rawKey).trim(), value: rawValue, line: line,
                         note: "not a number" });
      return;
    }

    if (soil) {
      var fixed = convert(soil.field, value, unit || soil.unitFromLabel || "");
      out.values[soil.field] = round(fixed.value);
      out.mapping.push({ key: soil.key, field: soil.field, value: round(fixed.value),
                         unit: unit || soil.unitFromLabel || "", note: fixed.note });
    } else {
      out.gps[gps.field] = gps.field === "gps_satellites" ? Math.round(value)
        : (gps.field === "latitude" || gps.field === "longitude") ? roundCoord(value)
        : round(value);
      out.mapping.push({ key: gps.key, field: gps.field, value: out.gps[gps.field],
                         unit: unit || "", note: "" });
    }
  }

  /* One line in, named values out. Nothing is inferred from position. */
  function parseLine(line) {
    var out = { values: {}, gps: {}, mapping: [], unknown: [], terminator: false };
    var text = String(line == null ? "" : line).trim();
    if (!text) return out;
    if (TERMINATORS.test(text)) { out.terminator = true; return out; }

    /* A sketch that prints JSON is the easy case and needs no label guessing. */
    if (text.charAt(0) === "{") {
      try {
        var obj = JSON.parse(text);
        var registers = null;
        Object.keys(obj).forEach(function (key) {
          if (IGNORE_KEYS[normaliseKey(key)]) return;
          if (key === "registers" && Object.prototype.toString.call(obj[key]) === "[object Array]") {
            registers = obj[key];
            return;
          }
          take(out, key, obj[key], "", text);
        });
        /* The raw block fills only what the named fields did not, so an explicit
           value always wins over one decoded from a map still marked provisional. */
        if (registers) decodeRegisters(out, registers);
        return out;
      } catch (e) { /* not JSON after all; fall through to the label scan */ }
    }

    /* The two lookaheads after the unit are load-bearing, and both come from real
       mis-reads of this format:
         (?![A-Za-z%...]) - a unit has to be a whole word. Without it the engine
           backtracks into half a label: in "LAT: 27.1 LON: 78.0" it took "LO" as the
           unit of 27.1, leaving the key "N" - and filed the LONGITUDE as NITROGEN.
         (?!\s*[:=])     - a word followed by a separator is the next label, not a
           unit. Without it "N: 45 P: 20" read P as the unit of 45 and phosphorus
           never reached the page at all. */
    /* The fix flag is a word, not a number, so the numeric scan below never sees it.
       GPS_FIX=NO is how this sketch reports "no satellites yet", and surfacing that
       is the difference between an empty location panel and one that explains why. */
    var flag = /gps[_ ]?fix\s*[:=]\s*([A-Za-z01]+)/i.exec(text);
    if (flag) {
      out.gps.gps_fix = /^(1|true|yes|valid|ok|fix|y)$/i.test(flag[1]);
      out.mapping.push({ key: "gps_fix", field: "gps_fix", value: out.gps.gps_fix,
                         unit: "", note: out.gps.gps_fix ? "" : "no satellite fix yet" });
    }

    var pairs = /([A-Za-z][A-Za-z0-9_().\/ -]{0,28}?)\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z%µ°][A-Za-z%µ°\/]{0,7})?(?![A-Za-z%µ°\/])(?!\s*[:=])/g;
    var found = flag ? 1 : 0, match;
    while ((match = pairs.exec(text)) !== null) {
      found += 1;
      take(out, match[1], match[2], match[3] || "", text);
    }
    if (found) return out;

    /* No colons or equals signs anywhere: accept "N 45  P 20" style, where the label
       still has to resolve. A line of bare numbers resolves nothing and is reported. */
    /* No unit is read here. With only spaces as separators there is nothing to
       distinguish "N 45 mg" from "N 45  P", and reading P as a unit would drop
       phosphorus silently. Unit conversion needs the labelled form above. */
    var spaced = /([A-Za-z][A-Za-z0-9_().\/-]{0,20})\s+(-?\d+(?:\.\d+)?)/g;
    while ((match = spaced.exec(text)) !== null) {
      take(out, match[1], match[2], "", text);
    }
    return out;
  }

  function decodeRegisters(out, registers) {
    for (var i = 0; i < REGISTER_ORDER.length && i < registers.length; i++) {
      var spec = REGISTER_ORDER[i];
      if (spec.field in out.values) continue;          /* a named value already won */
      var raw = parseFloat(registers[i]);
      if (!isFinite(raw)) continue;
      out.values[spec.field] = round(raw * spec.scale);
      out.mapping.push({ key: "register " + i, field: spec.field,
                         value: out.values[spec.field], unit: "",
                         note: "decoded with the provisional register map" });
    }
  }

  /* The explicit opt-in: bare comma-separated numbers in an order the operator states.
     Never reached by auto-detection - the dashboard only calls this when the format
     has been chosen by hand, and it shows the order it is applying. */
  function parseOrdered(line, order) {
    var out = { values: {}, gps: {}, mapping: [], unknown: [], terminator: false };
    var numbers = String(line == null ? "" : line).split(/[,;\s]+/)
      .map(function (t) { return t.trim(); }).filter(function (t) { return t !== ""; });
    if (!numbers.length || numbers.some(function (t) { return !/^-?\d+(\.\d+)?$/.test(t); })) {
      return out;                                  /* not a pure number line; ignore */
    }
    for (var i = 0; i < order.length && i < numbers.length; i++) {
      var field = order[i];
      if (FIELDS.indexOf(field) < 0) continue;
      out.values[field] = round(parseFloat(numbers[i]));
      out.mapping.push({ key: "position " + (i + 1), field: field,
                         value: out.values[field], unit: "",
                         note: "by the order you chose, not a label from the device" });
    }
    return out;
  }

  /* Accumulates lines into one frame. The Arduino sends a burst every 20 s, so a
     frame ends on a terminator line, when all seven parameters have arrived, or when
     a field repeats - which means the next cycle has started. Time is passed in
     rather than read, so a test can drive it without waiting 20 seconds. */
  function Frame() {
    this.values = {};
    this.gps = {};
    this.mapping = [];
    this.unknown = [];
    this.lines = [];
    this.startedAt = null;
    this.lastAt = null;
    this.complete = false;
  }

  Frame.prototype.feed = function (line, nowMs, options) {
    var opts = options || {};
    var parsed = opts.order && opts.order.length ? parseOrdered(line, opts.order)
                                                 : parseLine(line);
    var self = this;
    var restarted = Object.keys(parsed.values).some(function (field) {
      return Object.prototype.hasOwnProperty.call(self.values, field);
    });
    if (restarted) { this.complete = true; return { parsed: parsed, restarted: true }; }

    if (this.startedAt === null) this.startedAt = nowMs;
    this.lastAt = nowMs;
    if (String(line || "").trim()) this.lines.push(String(line).trim());

    Object.keys(parsed.values).forEach(function (f) { self.values[f] = parsed.values[f]; });
    Object.keys(parsed.gps).forEach(function (f) { self.gps[f] = parsed.gps[f]; });
    this.mapping = this.mapping.concat(parsed.mapping);
    this.unknown = this.unknown.concat(parsed.unknown);

    if (parsed.terminator && Object.keys(this.values).length) this.complete = true;
    if (Object.keys(this.values).length >= FIELDS.length) this.complete = true;
    return { parsed: parsed, restarted: false };
  };

  /* True when the burst has clearly finished: something arrived, and the line has
     been quiet since. Without this, a frame read mid-burst would show three of the
     seven values and look like a half-broken sensor. */
  Frame.prototype.settled = function (nowMs, idleMs) {
    if (!Object.keys(this.values).length) return false;
    if (this.complete) return true;
    return nowMs - this.lastAt >= (idleMs || 700);
  };

  Frame.prototype.snapshot = function () {
    var self = this;
    return {
      values: JSON.parse(JSON.stringify(this.values)),
      gps: JSON.parse(JSON.stringify(this.gps)),
      mapping: this.mapping.slice(),
      unknown: this.unknown.slice(),
      raw: this.lines.join("\n"),
      received: Object.keys(this.values).sort(),
      missing: FIELDS.filter(function (f) { return !(f in self.values); }),
      at: this.lastAt
    };
  };

  var BleParse = {
    FIELDS: FIELDS,
    FIELD_ALIASES: FIELD_ALIASES,
    GPS_ALIASES: GPS_ALIASES,
    normaliseKey: normaliseKey,
    resolveKey: resolveKey,
    parseLine: parseLine,
    parseOrdered: parseOrdered,
    REGISTER_ORDER: REGISTER_ORDER,
    Frame: Frame
  };

  if (typeof module !== "undefined" && module.exports) module.exports = BleParse;
  else global.BleParse = BleParse;
})(typeof self !== "undefined" ? self : globalThis);
