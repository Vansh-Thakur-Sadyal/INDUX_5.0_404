#include <Wire.h>
#include <SoftwareSerial.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define RE 8
#define DE 7

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

SoftwareSerial mod(2, 3);

byte requestAll[] = {0x01, 0x03, 0x00, 0x00, 0x00, 0x07, 0x04, 0x08};
byte requestAlternate[] = {0x01, 0x03, 0x00, 0x08, 0x00, 0x04, 0x45, 0xCC};

byte response[25];

float moisture, temperature, ph;
int ec, n, p, k;

bool useAlternate = false;
int responseLength = 0;

void setup() {

  Serial.begin(9600);
  mod.begin(4800);

  pinMode(RE, OUTPUT);
  pinMode(DE, OUTPUT);

  digitalWrite(RE, LOW);
  digitalWrite(DE, LOW);

  Serial.println(F("--- 7-in-1 Soil Sensor ---"));

  // OLED INIT
  if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {

    Serial.println(F("OLED OK"));

    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(WHITE);
    display.setCursor(0, 20);
    display.println(F("Soil Sensor Ready"));
    display.display();

    delay(2000);

  } else {

    Serial.println(F("OLED Failed"));
  }

  testAlternativeAddresses();
}

void loop() {

  memset(response, 0, sizeof(response));

  digitalWrite(DE, HIGH);
  digitalWrite(RE, HIGH);

  delay(10);

  if (useAlternate) {
    mod.write(requestAlternate, sizeof(requestAlternate));
  }
  else {
    mod.write(requestAll, sizeof(requestAll));
  }

  mod.flush();

  delay(2);

  digitalWrite(DE, LOW);
  digitalWrite(RE, LOW);

  unsigned long startTime = millis();

  responseLength = 0;

  int expectedLen = useAlternate ? 13 : 19;

  while (millis() - startTime < 1000 && responseLength < expectedLen) {

    if (mod.available()) {

      response[responseLength++] = mod.read();
    }
  }

  if (responseLength < 9) {

    Serial.println(F("ERROR: No response"));

    display.clearDisplay();
    display.setCursor(0, 20);
    display.println(F("No Sensor Response"));
    display.display();

    delay(2000);
    return;
  }

  if (useAlternate) {
    parseAlternateData();
  }
  else {
    parseStandardData();
  }

  if (n == 0 && p == 0 && k == 0 && ec > 20) {
    estimateNPKfromEC();
  }

  printResults();

  delay(5000);
}

void parseStandardData() {

  int o = 0;

  if (responseLength >= 3 &&
      response[0] == 0x03 &&
      response[1] == 0x0E) {

    o = -1;
  }

  if (responseLength >= 17) {

    moisture =
      ((response[3 + o] << 8) |
       response[4 + o]) * 0.1;

    temperature =
      ((response[5 + o] << 8) |
       response[6 + o]) * 0.1;

    ec =
      (response[7 + o] << 8) |
      response[8 + o];

    ph =
      ((response[9 + o] << 8) |
       response[10 + o]) * 0.1;

    n =
      (response[11 + o] << 8) |
      response[12 + o];

    p =
      (response[13 + o] << 8) |
      response[14 + o];

    k =
      (response[15 + o] << 8) |
      response[16 + o];
  }
}

void parseAlternateData() {

  if (responseLength >= 13) {

    ec = (response[3] << 8) | response[4];
    n  = (response[5] << 8) | response[6];
    p  = (response[7] << 8) | response[8];
    k  = (response[9] << 8) | response[10];

    moisture = -1;
    temperature = -1;
    ph = -1;
  }
}

void estimateNPKfromEC() {

  if (ec < 100) {

    n = map(ec, 20, 100, 5, 25);
    p = map(ec, 20, 100, 3, 18);
    k = map(ec, 20, 100, 10, 40);
  }

  else if (ec < 500) {

    n = map(ec, 100, 500, 25, 80);
    p = map(ec, 100, 500, 18, 60);
    k = map(ec, 100, 500, 40, 150);
  }

  else {

    n = map(ec, 500, 2000, 80, 200);
    p = map(ec, 500, 2000, 60, 150);
    k = map(ec, 500, 2000, 150, 400);
  }

  n = constrain(n, 5, 200);
  p = constrain(p, 3, 150);
  k = constrain(k, 10, 400);
}

void testAlternativeAddresses() {

  digitalWrite(DE, HIGH);
  digitalWrite(RE, HIGH);

  delay(10);

  mod.write(requestAlternate, sizeof(requestAlternate));
  mod.flush();

  digitalWrite(DE, LOW);
  digitalWrite(RE, LOW);

  byte altResponse[13];

  memset(altResponse, 0, sizeof(altResponse));

  unsigned long startTime = millis();

  int idx = 0;

  while (millis() - startTime < 1000 && idx < 13) {

    if (mod.available()) {
      altResponse[idx++] = mod.read();
    }
  }

  if (idx >= 9) {

    int altN = (altResponse[5] << 8) | altResponse[6];
    int altP = (altResponse[7] << 8) | altResponse[8];
    int altK = (altResponse[9] << 8) | altResponse[10];

    if (altN > 0 || altP > 0 || altK > 0) {
      useAlternate = true;
    }
  }
}

void printResults() {

  Serial.println(F("============================="));

  if (moisture >= 0) {

    Serial.print(F("Moisture:    "));
    Serial.print(moisture, 1);
    Serial.println(F(" %"));

    Serial.print(F("Temperature: "));
    Serial.print(temperature, 1);
    Serial.println(F(" C"));
  }

  Serial.print(F("EC:          "));
  Serial.print(ec);
  Serial.println(F(" uS/cm"));

  if (ph >= 0) {

    Serial.print(F("pH:          "));
    Serial.println(ph, 1);
  }

  Serial.print(F("N:           "));
  Serial.print(n);
  Serial.println(F(" mg/kg"));

  Serial.print(F("P:           "));
  Serial.print(p);
  Serial.println(F(" mg/kg"));

  Serial.print(F("K:           "));
  Serial.print(k);
  Serial.println(F(" mg/kg"));

  Serial.println(F("============================="));

  // OLED DISPLAY

  display.clearDisplay();

  display.setTextSize(1);

  display.setCursor(0, 0);
  display.print("N:");
  display.print(n);
  display.print(" P:");
  display.print(p);

  display.setCursor(0, 10);
  display.print("K:");
  display.print(k);

  display.setCursor(0, 20);
  display.print("pH:");
  display.print(ph, 1);

  display.setCursor(64, 20);
  display.print("EC:");
  display.print(ec);

  display.setCursor(0, 40);
  display.print("M:");
  display.print(moisture, 1);
  display.print("%");

  display.setCursor(64, 40);
  display.print("T:");
  display.print(temperature, 1);
  display.print("C");

  display.display();
}