/* The BLE link to the HM-10, over Web Bluetooth.

   The browser holds the radio; the server never sees it. The phone or laptop running
   the dashboard pairs with the HM-10 exactly as the Serial Bluetooth Terminal app
   does, reads the same lines, and posts the parsed packet to the backend.

   Two things about this file are deliberate.

   The first is chunking. An HM-10 hands over about 20 bytes per notification, so a
   line arrives in pieces and two lines can share one notification. Everything is
   appended to a buffer and only split on a newline, otherwise "N: 45" shows up as
   "N: 4" and "5".

   The second is that the stream does NOT drive the dashboard. Notifications keep
   filling a frame in the background, but the numbers on the page change only when
   capture() is called - the fetch button. That is what was asked for: fetch once, read
   those values, and have them stay put until fetch is pressed again. */
(function (global) {
  "use strict";

  var Parse = global.BleParse;

  /* HM-10 and its common clones (BT05, MLT-BT05, HMSoft) all expose the same
     transparent serial service. 16-bit FFE0/FFE1 expanded to full UUIDs. */
  var SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb";
  var CHARACTERISTIC = "0000ffe1-0000-1000-8000-00805f9b34fb";
  var NAME_PREFIXES = ["HM", "HMSoft", "BT05", "MLT-BT05", "AT-09", "JDY", "Saarthi"];

  /* The sketch sends every 20 s, so a capture may legitimately have to wait that
     long. 40 s allows one full cycle plus the slack of a missed burst. */
  var CAPTURE_TIMEOUT_MS = 40000;
  var FRAME_IDLE_MS = 700;
  var FRESH_ENOUGH_MS = 25000;
  var MAX_RAW_LINES = 200;

  var listeners = { line: [], status: [], frame: [], gps: [] };
  var link = {
    device: null,
    characteristic: null,
    connected: false,
    deviceName: "",
    buffer: "",
    rawLines: [],
    frame: null,
    lastFrame: null,        /* the most recent settled frame, waiting to be fetched */
    gps: {},                /* running position, NOT tied to a soil frame - see below */
    order: null,            /* set only when the operator picks the positional format */
    idleTimer: null,
    waiter: null,
    stats: { lines: 0, frames: 0, connectedAt: null }
  };

  function emit(kind, payload) {
    (listeners[kind] || []).forEach(function (fn) {
      try { fn(payload); } catch (e) { /* a broken listener must not kill the link */ }
    });
  }

  function on(kind, fn) {
    if (listeners[kind]) listeners[kind].push(fn);
  }

  function supported() {
    return typeof navigator !== "undefined" && !!navigator.bluetooth;
  }

  /* Why a connection might be impossible before the user even presses the button.
     Saying this up front is better than letting requestDevice fail with a bare
     "NotFoundError" that reads like the sensor is broken. */
  function unsupportedReason() {
    if (typeof navigator === "undefined") return "No browser environment.";
    if (navigator.bluetooth) return "";
    var secure = typeof window !== "undefined" && window.isSecureContext;
    if (!secure) {
      return "Web Bluetooth needs HTTPS. Open the dashboard over https:// "
           + "(or http://localhost while developing) and try again.";
    }
    var ua = String(navigator.userAgent || "");
    if (/iPhone|iPad|iPod/.test(ua) || /Safari/.test(ua) && !/Chrome|Chromium|Edg/.test(ua)) {
      return "Safari and iOS do not support Web Bluetooth. Use Chrome or Edge on "
           + "Android, Windows, macOS or Linux to read the HM-10 from this page.";
    }
    if (/Firefox/.test(ua)) {
      return "Firefox does not support Web Bluetooth. Use Chrome or Edge.";
    }
    return "This browser does not expose Web Bluetooth.";
  }

  function status(text, kind) {
    emit("status", { text: text, kind: kind || "info", connected: link.connected,
                     device: link.deviceName });
  }

  /* ------------------------------------------------------------- connect */
  async function connect(options) {
    var opts = options || {};
    if (!supported()) throw new Error(unsupportedReason());

    status("Opening the device chooser...", "info");
    var request = opts.anyDevice
      ? { acceptAllDevices: true, optionalServices: [SERVICE] }
      : {
          filters: [{ services: [SERVICE] }].concat(
            NAME_PREFIXES.map(function (p) { return { namePrefix: p }; })),
          optionalServices: [SERVICE]
        };

    var device = await navigator.bluetooth.requestDevice(request);
    link.device = device;
    link.deviceName = device.name || "HM-10";
    device.addEventListener("gattserverdisconnected", onDisconnected);

    status("Connecting to " + link.deviceName + "...", "info");
    var server = await device.gatt.connect();
    var service = await server.getPrimaryService(SERVICE);
    var characteristic = await service.getCharacteristic(CHARACTERISTIC);
    await characteristic.startNotifications();
    characteristic.addEventListener("characteristicvaluechanged", onValue);

    link.characteristic = characteristic;
    link.connected = true;
    link.buffer = "";
    link.frame = new Parse.Frame();
    link.stats.connectedAt = Date.now();
    status("Connected to " + link.deviceName + ". Listening for packets.", "ok");
    return { device: link.deviceName };
  }

  function onDisconnected() {
    link.connected = false;
    link.characteristic = null;
    clearTimeout(link.idleTimer);
    /* A capture in flight has to fail rather than hang until its timeout. */
    if (link.waiter) {
      link.waiter.reject(new Error("The device disconnected before a packet arrived."));
      link.waiter = null;
    }
    status("Disconnected from " + (link.deviceName || "the device") + ".", "warn");
  }

  async function disconnect() {
    clearTimeout(link.idleTimer);
    if (link.device && link.device.gatt && link.device.gatt.connected) {
      link.device.gatt.disconnect();          /* fires gattserverdisconnected */
    } else {
      onDisconnected();
    }
  }

  /* --------------------------------------------------------- the stream */
  var decoder = typeof TextDecoder !== "undefined" ? new TextDecoder() : null;

  function onValue(event) {
    var value = event.target.value;
    var text = decoder ? decoder.decode(value) : "";
    ingest(text);
  }

  /* Exposed so the test page and the tests can push text through the exact path a
     notification takes, without a radio. */
  function ingest(text, nowMs) {
    var now = nowMs === undefined ? Date.now() : nowMs;
    link.buffer += String(text == null ? "" : text);
    /* Only split on a newline: a notification boundary is not a line boundary. */
    var parts = link.buffer.split(/\r?\n/);
    link.buffer = parts.pop();                /* the unfinished tail stays buffered */
    parts.forEach(function (line) { handleLine(line, now); });
    scheduleIdleFlush(now);
  }

  function handleLine(line, now) {
    link.stats.lines += 1;
    link.rawLines.push(line);
    if (link.rawLines.length > MAX_RAW_LINES) link.rawLines.shift();
    emit("line", { line: line, at: now });

    if (!link.frame) link.frame = new Parse.Frame();
    var result = link.frame.feed(line, now, { order: link.order });

    /* The GPS is merged here, outside the frame, and kept until something newer
       arrives. A frame carrying only a position never closes - closeFrame needs a soil
       value - so anything held in the frame alone would be parsed and then lost. */
    var gps = result.parsed.gps || {};
    if (Object.keys(gps).length) {
      Object.keys(gps).forEach(function (key) { link.gps[key] = gps[key]; });
      link.gps.at = now;
      emit("gps", positionSnapshot());
    }

    if (result.restarted) {
      /* The next cycle has begun: close this frame and start the new one with the
         line that triggered it, so the first value of the cycle is not lost. */
      closeFrame(now);
      link.frame.feed(line, now, { order: link.order });
      return;
    }
    if (link.frame.complete) closeFrame(now);
  }

  function scheduleIdleFlush(now) {
    clearTimeout(link.idleTimer);
    if (!link.frame || !Object.keys(link.frame.values).length) return;
    link.idleTimer = setTimeout(function () {
      if (link.frame && link.frame.settled(Date.now(), FRAME_IDLE_MS)) closeFrame(Date.now());
    }, FRAME_IDLE_MS + 50);
  }

  function positionSnapshot() {
    var out = {};
    Object.keys(link.gps).forEach(function (key) {
      if (key !== "at") out[key] = link.gps[key];
    });
    return out;
  }

  function closeFrame(now) {
    if (!link.frame || !Object.keys(link.frame.values).length) return;
    var snapshot = link.frame.snapshot();
    /* The position as it stands now, not whatever happened to share the frame. */
    snapshot.gps = positionSnapshot();
    snapshot.closedAt = now;
    link.lastFrame = snapshot;
    link.stats.frames += 1;
    link.frame = new Parse.Frame();
    emit("frame", snapshot);
    if (link.waiter) {
      var waiter = link.waiter;
      link.waiter = null;
      clearTimeout(waiter.timer);
      waiter.resolve(snapshot);
    }
  }

  /* --------------------------------------------------------- the fetch */
  /* Take exactly one packet. If a complete one arrived in the last few seconds it is
     used straight away; otherwise this waits for the next burst. Either way it
     resolves once, and nothing else on the page moves until it is called again. */
  function capture(options) {
    var opts = options || {};
    var timeoutMs = opts.timeoutMs || CAPTURE_TIMEOUT_MS;
    return new Promise(function (resolve, reject) {
      if (!link.connected) {
        reject(new Error("Not connected. Press Connect device first."));
        return;
      }
      if (link.waiter) {
        reject(new Error("A fetch is already waiting for the next packet."));
        return;
      }
      var fresh = link.lastFrame
        && (Date.now() - link.lastFrame.closedAt) <= (opts.freshMs || FRESH_ENOUGH_MS);
      if (fresh && !opts.forceWait) {
        resolve(link.lastFrame);
        return;
      }
      /* Mid-burst right now: let the frame finish instead of reading half of it. */
      var timer = setTimeout(function () {
        link.waiter = null;
        reject(new Error("No complete packet in " + Math.round(timeoutMs / 1000)
          + " s. The sketch sends every 20 s - check the HM-10 is powered and that "
          + "the Serial Bluetooth Terminal app is closed, since only one device can "
          + "hold the link."));
      }, timeoutMs);
      link.waiter = { resolve: resolve, reject: reject, timer: timer };
    });
  }

  function setOrder(order) {
    link.order = order && order.length ? order.slice() : null;
  }

  function info() {
    return {
      supported: supported(),
      reason: unsupportedReason(),
      connected: link.connected,
      device: link.deviceName,
      lines: link.stats.lines,
      frames: link.stats.frames,
      connectedAt: link.stats.connectedAt,
      rawLines: link.rawLines.slice(),
      lastFrame: link.lastFrame,
      gps: positionSnapshot(),
      order: link.order ? link.order.slice() : null,
      service: SERVICE,
      characteristic: CHARACTERISTIC
    };
  }

  /* Used by the tests and by the "paste a sample" box on the page, which lets the
     parser and the whole fetch path be checked without the hardware present. */
  function reset() {
    clearTimeout(link.idleTimer);
    link.buffer = "";
    link.rawLines = [];
    link.frame = new Parse.Frame();
    link.lastFrame = null;
    link.gps = {};
    link.waiter = null;
    link.stats = { lines: 0, frames: 0, connectedAt: link.stats.connectedAt };
  }

  function simulateConnected(name) {
    link.connected = true;
    link.deviceName = name || "sample text";
    link.frame = link.frame || new Parse.Frame();
  }

  global.SaarthiBLE = {
    SERVICE: SERVICE,
    CHARACTERISTIC: CHARACTERISTIC,
    supported: supported,
    unsupportedReason: unsupportedReason,
    connect: connect,
    disconnect: disconnect,
    capture: capture,
    ingest: ingest,
    setOrder: setOrder,
    info: info,
    reset: reset,
    simulateConnected: simulateConnected,
    on: on
  };
})(typeof self !== "undefined" ? self : globalThis);
