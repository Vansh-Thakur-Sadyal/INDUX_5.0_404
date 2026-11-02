/*
 * Saarthi 2.0 - 7-in-1 soil sensor -> Arduino Uno -> HM-10 BLE -> Android gateway
 * -----------------------------------------------------------------------------
 * Reads seven holding registers from an RS485 Modbus RTU soil probe and pushes one
 * compact line over BLE. The Android companion app forwards that line to
 *     POST <saarthi>/api/sensors/ingest      header: X-Device-Token
 *
 * WIRING (check your own boards before powering anything)
 *   RS485 module (MAX485)        Arduino Uno
 *     RO  ---------------------->  D2   (SoftwareSerial RX)
 *     DI  <----------------------  D3   (SoftwareSerial TX)
 *     DE + RE -------------------  D4   (direction, HIGH = transmit)
 *     VCC -----------------------  5V        GND --- GND
 *     A / B ---------------------  sensor A / B
 *   Sensor power: most 7-in-1 probes want 9-24 V DC on a separate supply.
 *     Share GND with the Arduino; do NOT run the probe from the 5V pin.
 *   HM-10 BLE                     Arduino Uno
 *     TXD ----------------------->  D10  (SoftwareSerial RX)
 *     RXD <--- 1k --+------------  D11  (SoftwareSerial TX)
 *                   |
 *                  2k
 *                   |
 *                  GND            <-- divider: Uno is 5 V logic, HM-10 RX is 3.3 V.
 *     VCC ---------------------- 3.3V (or 5V only if the board has a regulator)
 *
 * PROVISIONAL REGISTER MAP - confirm against your sensor manual before trusting a value.
 * The backend serves the same map at GET /api/sensors/register-map.
 *   0x0000 moisture   x0.1  %        0x0004 nitrogen    x1  mg/kg
 *   0x0001 temperature x0.1 C        0x0005 phosphorus  x1  mg/kg
 *   0x0002 EC         x1    uS/cm    0x0006 potassium   x1  mg/kg
 *   0x0003 pH         x0.1  pH
 * Typical probe defaults: 9600 baud, 8N1, slave id 0x01. Change below if yours differ.
 */

#include <SoftwareSerial.h>

const uint8_t  SLAVE_ID      = 0x01;
const uint16_t START_REG     = 0x0000;
const uint16_t REG_COUNT     = 7;
const long     RS485_BAUD    = 9600;
const long     BLE_BAUD      = 9600;
const unsigned long INTERVAL = 60000UL;     // one reading per minute
const char*    DEVICE_ID     = "7IN1-UNO-01";
const char*    PARCEL_ID     = "field-1";

SoftwareSerial rs485(2, 3);                  // RO -> D2, DI -> D3
SoftwareSerial ble(10, 11);                  // HM-10 TXD -> D10, RXD -> D11
const uint8_t  RS485_DIR = 4;

uint16_t registers[REG_COUNT];
unsigned long lastRead = 0;

/* ---- Modbus RTU CRC16 (low byte first on the wire) ---- */
uint16_t crc16(const uint8_t *buf, uint8_t len) {
  uint16_t crc = 0xFFFF;
  for (uint8_t i = 0; i < len; i++) {
    crc ^= buf[i];
    for (uint8_t b = 0; b < 8; b++) {
      crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : crc >> 1;
    }
  }
  return crc;
}

/* ---- One Modbus read-holding-registers transaction ---- */
bool readSensor() {
  uint8_t req[8];
  req[0] = SLAVE_ID;
  req[1] = 0x03;                             // read holding registers
  req[2] = highByte(START_REG);
  req[3] = lowByte(START_REG);
  req[4] = highByte(REG_COUNT);
  req[5] = lowByte(REG_COUNT);
  uint16_t crc = crc16(req, 6);
  req[6] = lowByte(crc);
  req[7] = highByte(crc);

  rs485.listen();
  digitalWrite(RS485_DIR, HIGH);             // transmit
  delayMicroseconds(100);
  rs485.write(req, sizeof(req));
  rs485.flush();
  delayMicroseconds(100);
  digitalWrite(RS485_DIR, LOW);              // back to receive

  const uint8_t expected = 5 + REG_COUNT * 2;   // id + fn + len + data + crc
  uint8_t resp[32];
  uint8_t got = 0;
  unsigned long deadline = millis() + 1000;
  while (millis() < deadline && got < expected) {
    if (rs485.available()) resp[got++] = rs485.read();
  }
  if (got < expected) return false;                         // timeout / short frame
  if (resp[0] != SLAVE_ID || resp[1] != 0x03) return false; // wrong device or exception
  uint16_t want = crc16(resp, expected - 2);
  if (resp[expected - 2] != lowByte(want) || resp[expected - 1] != highByte(want)) {
    return false;                                           // corrupt frame: drop it
  }
  for (uint8_t i = 0; i < REG_COUNT; i++) {
    registers[i] = (uint16_t)resp[3 + i * 2] << 8 | resp[4 + i * 2];
  }
  return true;
}

/* ---- Emit one BLE line. Raw registers are sent too, so the backend can verify
        the scaling itself instead of trusting the sketch. ---- */
void sendPacket(bool ok) {
  ble.listen();
  ble.print(F("{\"device_id\":\""));  ble.print(DEVICE_ID);
  ble.print(F("\",\"parcel_id\":\"")); ble.print(PARCEL_ID);
  ble.print(F("\",\"mode\":\"real\",\"ok\":")); ble.print(ok ? F("true") : F("false"));
  if (ok) {
    ble.print(F(",\"moisture_pct\":"));    ble.print(registers[0] / 10.0, 1);
    ble.print(F(",\"temperature_c\":"));   ble.print(registers[1] / 10.0, 1);
    ble.print(F(",\"ec_ds_m\":"));         ble.print(registers[2] / 1000.0, 3);
    ble.print(F(",\"ph\":"));              ble.print(registers[3] / 10.0, 1);
    ble.print(F(",\"nitrogen_mg_kg\":"));   ble.print(registers[4]);
    ble.print(F(",\"phosphorus_mg_kg\":")); ble.print(registers[5]);
    ble.print(F(",\"potassium_mg_kg\":"));  ble.print(registers[6]);
    ble.print(F(",\"registers\":["));
    for (uint8_t i = 0; i < REG_COUNT; i++) {
      ble.print(registers[i]);
      if (i < REG_COUNT - 1) ble.print(',');
    }
    ble.print(F("]"));
  } else {
    ble.print(F(",\"error\":\"modbus_read_failed\""));
  }
  ble.println('}');
}

void setup() {
  pinMode(RS485_DIR, OUTPUT);
  digitalWrite(RS485_DIR, LOW);
  rs485.begin(RS485_BAUD);
  ble.begin(BLE_BAUD);
  delay(1500);                               // let the probe settle after power-up
  sendPacket(readSensor());
  lastRead = millis();
}

void loop() {
  if (millis() - lastRead >= INTERVAL) {
    sendPacket(readSensor());
    lastRead = millis();
  }
}

/*
 * ANDROID GATEWAY (what the companion app has to do)
 * --------------------------------------------------
 * 1. Scan for the HM-10 (service FFE0, characteristic FFE1), connect, enable
 *    notifications. Lines arrive in 20-byte chunks: buffer until '\n'.
 * 2. Add the phone's clock as recorded_at in ISO-8601 UTC; the Uno has no RTC, and the
 *    backend marks a reading stale after 6 hours, so the timestamp must be real.
 * 3. POST the JSON:
 *
 *      POST https://<saarthi>/api/sensors/ingest
 *      X-Device-Token: <SAARTHI_INBOUND_TOKEN>
 *      Content-Type: application/json
 *
 *      { "device_id": "7IN1-UNO-01", "parcel_id": "field-1", "mode": "real",
 *        "recorded_at": "2026-01-20T10:30:00Z",
 *        "registers": [420, 241, 800, 68, 32, 18, 210] }
 *
 *    Either send the decoded fields or just "registers" - the backend decodes the block
 *    with the same provisional map and validates either way.
 * 4. Queue packets while offline and re-send; the backend de-duplicates by timestamp order
 *    and never clamps an out-of-range value, it rejects it and says which field failed.
 * 5. Never send mode "simulation" to this endpoint - it returns 400. Simulated readings
 *    only ever come from /api/sensors/simulate, so a test value cannot enter the real path.
 */
