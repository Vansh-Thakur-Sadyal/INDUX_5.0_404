(function () {
  const SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb";
  const CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb";
  const FIELD_NAMES = {
    N: "n",
    P: "p",
    K: "k",
    TEMP_C: "temp",
    MOISTURE_PCT: "moisture",
    EC_US_CM: "ec",
    PH: "ph"
  };
  let device = null;
  let characteristic = null;
  let lineBuffer = "";
  let onReading = null;
  let onDisconnect = null;

  function parseTelemetryLine(line) {
    const fields = {};
    for (const pair of line.split(",")) {
      const match = pair.trim().match(/^([A-Z_]+)\s*=\s*(-?\d+(?:\.\d+)?)$/i);
      if (!match) return null;
      const key = match[1].toUpperCase();
      if (!(key in FIELD_NAMES)) return null;
      fields[FIELD_NAMES[key]] = Number(match[2]);
    }

    if (Object.keys(FIELD_NAMES).length !== Object.keys(fields).length) return null;
    if (Object.values(fields).some(value => !Number.isFinite(value))) return null;
    for (const key of ["moisture", "temp", "ph"]) {
      if (fields[key] === -1) fields[key] = null;
    }
    if ((fields.moisture !== null && (fields.moisture < 0 || fields.moisture > 100)) ||
        (fields.ph !== null && (fields.ph < 0 || fields.ph > 14)) ||
        (fields.temp !== null && (fields.temp < -20 || fields.temp > 100)) ||
        fields.n < 0 || fields.p < 0 || fields.k < 0 || fields.ec < 0) return null;
    return fields;
  }

  function handleValueChanged(event) {
    const value = event.target.value;
    const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    lineBuffer += new TextDecoder().decode(bytes);
    const lines = lineBuffer.split(/\r?\n/);
    lineBuffer = lines.pop() || "";

    for (const line of lines) {
      const reading = parseTelemetryLine(line.trim());
      if (reading && onReading) onReading(reading);
    }
  }

  function handleDisconnected() {
    characteristic = null;
    lineBuffer = "";
    if (onDisconnect) onDisconnect();
  }

  async function connect(readingHandler, disconnectHandler) {
    if (!navigator.bluetooth) {
      throw new Error("Web Bluetooth is unavailable. Use Chrome or Edge on localhost/HTTPS.");
    }

    onReading = readingHandler;
    onDisconnect = disconnectHandler;
    if (device && device.gatt && device.gatt.connected && characteristic) return device;

    device = await navigator.bluetooth.requestDevice({
      filters: [
        { services: [SERVICE_UUID] },
        { namePrefix: "HMSoft" },
        { namePrefix: "HM-10" },
        { namePrefix: "HM10" },
        { namePrefix: "BT05" }
      ],
      optionalServices: [SERVICE_UUID]
    });
    device.addEventListener("gattserverdisconnected", handleDisconnected);

    try {
      const server = await device.gatt.connect();
      const service = await server.getPrimaryService(SERVICE_UUID);
      characteristic = await service.getCharacteristic(CHARACTERISTIC_UUID);
      characteristic.addEventListener("characteristicvaluechanged", handleValueChanged);
      await characteristic.startNotifications();
      lineBuffer = "";
      return device;
    } catch (error) {
      if (characteristic) {
        characteristic.removeEventListener("characteristicvaluechanged", handleValueChanged);
      }
      if (device.gatt && device.gatt.connected) device.gatt.disconnect();
      characteristic = null;
      onReading = null;
      onDisconnect = null;
      throw error;
    }
  }

  function disconnect() {
    if (characteristic) {
      characteristic.removeEventListener("characteristicvaluechanged", handleValueChanged);
    }
    if (device && device.gatt && device.gatt.connected) device.gatt.disconnect();
    characteristic = null;
    lineBuffer = "";
    onReading = null;
    onDisconnect = null;
  }

  window.HM10SoilSensor = { connect, disconnect };
})();
