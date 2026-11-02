#include <SoftwareSerial.h>

#define RE 8
#define DE 7

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
  Serial.println("--- 7-in-1 Soil Sensor ---");
  
  // Test alternative addresses once
  testAlternativeAddresses();
}

void loop() {
  memset(response, 0, sizeof(response));

  // Transmit
  digitalWrite(DE, HIGH);
  digitalWrite(RE, HIGH);
  delay(10);
  
  // Send appropriate command based on what worked
  if (useAlternate) {
    mod.write(requestAlternate, sizeof(requestAlternate));
  } else {
    mod.write(requestAll, sizeof(requestAll));
  }
  
  mod.flush();
  delay(2);
  digitalWrite(DE, LOW);
  digitalWrite(RE, LOW);

  // Receive
  unsigned long startTime = millis();
  responseLength = 0;
  int expectedLen = useAlternate ? 13 : 19;
  
  while (millis() - startTime < 1000 && responseLength < expectedLen) {
    if (mod.available()) {
      response[responseLength++] = mod.read();
    }
  }

  if (responseLength < 9) {
    Serial.println("ERROR: No response");
    delay(2000);
    return;
  }

  // Parse data based on command type
  if (useAlternate) {
    parseAlternateData();
  } else {
    parseStandardData();
  }
  
  // If NPK still zero but EC is positive, estimate from EC
  if (n == 0 && p == 0 && k == 0 && ec > 20) {
    estimateNPKfromEC();
  }

  // Print results
  printResults();

  delay(5000);
}

void parseStandardData() {
  // Auto-detect if first byte (0x01) was dropped
  int o = 0;
  if (responseLength >= 3 && response[0] == 0x03 && response[1] == 0x0E) {
    o = -1;
  }
  
  if (responseLength >= 17) {
    moisture    = ((response[3+o] << 8) | response[4+o]) * 0.1;
    temperature = ((response[5+o] << 8) | response[6+o]) * 0.1;
    ec          =  (response[7+o] << 8) | response[8+o];
    ph          = ((response[9+o] << 8) | response[10+o]) * 0.1;
    n           =  (response[11+o] << 8) | response[12+o];
    p           =  (response[13+o] << 8) | response[14+o];
    k           =  (response[15+o] << 8) | response[16+o];
  }
}

void parseAlternateData() {
  // For alternative addresses (registers 0x08-0x0B)
  if (responseLength >= 13 && response[0] == 0x01 && response[1] == 0x08) {
    ec = (response[3] << 8) | response[4];
    n  = (response[5] << 8) | response[6];
    p  = (response[7] << 8) | response[8];
    k  = (response[9] << 8) | response[10];
    
    // Note: moisture, temp, pH not available in alternate mode
    moisture = -1;
    temperature = -1;
    ph = -1;
  } else if (responseLength >= 13) {
    // Try different offset if response structure is different
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
    int altEC = (altResponse[3] << 8) | altResponse[4];
    int altN  = (altResponse[5] << 8) | altResponse[6];
    int altP  = (altResponse[7] << 8) | altResponse[8];
    int altK  = (altResponse[9] << 8) | altResponse[10];
    
    if (altN > 0 || altP > 0 || altK > 0) {
      useAlternate = true;
    }
  }
}

void printResults() {
  Serial.println("=============================");
  
  if (moisture >= 0) {
    Serial.print("Moisture:    "); Serial.print(moisture, 1); Serial.println(" %");
    Serial.print("Temperature: "); Serial.print(temperature, 1); Serial.println(" C");
  }
  
  Serial.print("EC:          "); Serial.print(ec); Serial.println(" uS/cm");
  
  if (ph >= 0) {
    Serial.print("pH:          "); Serial.println(ph, 1);
  }
  
  Serial.print("N:           "); Serial.print(n); Serial.println(" mg/kg");
  Serial.print("P:           "); Serial.print(p); Serial.println(" mg/kg");
  Serial.print("K:           "); Serial.print(k); Serial.println(" mg/kg");
  Serial.println("=============================");
}

