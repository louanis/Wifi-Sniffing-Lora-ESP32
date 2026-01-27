#include <WiFi.h>
#include <HTTPClient.h>
#include <HardwareSerial.h>

// ================= WIFI CONFIG =================
#define WIFI_SSID     "Quoicoubeh"
#define WIFI_PASSWORD "quoicouEchecScolaireBeh"

// ================= API CONFIG =================
#define DEVICE_ID "esp32-001"
#define LOCATE_URL "http://vps-98cd652a.vps.ovh.net:8067/scan/locate"
#define CALIBRATE_URL "http://vps-98cd652a.vps.ovh.net:8067/scan/calibrate"

// ================= LORA CONFIG =================
#define LORA_RX 16
#define LORA_TX 17
#define LORA_BAUD 9600

#define APPEUI "0000000000000000"
#define APPKEY "3D0D6AA79FA5CD474E71E9A92D533BCC"
#define DEVEUI "70B3D57ED0073262"

HardwareSerial LoRa(2);

// ================= PARAMETERS =================
const int MAX_AP_TO_SEND = 10;
const long sendInterval = 10000; // 10s

long lastSendTime = 0;

// ================= GLOBALS =================
bool coordsReceived = false;
float latitude = 0.0;
float longitude = 0.0;
bool isJoined = false;

// ================= HOTSPOT FILTER =================
bool isLikelyHotspot(String ssid, uint8_t* bssid) {
  uint8_t secondNibble = bssid[0] & 0x0F;
  if (secondNibble == 0x2 || secondNibble == 0x6 ||
      secondNibble == 0xA || secondNibble == 0xE) return true;

  String s = ssid;
  s.toUpperCase();
  if (s.indexOf("IPHONE") >= 0) return true;
  if (s.indexOf("ANDROID") >= 0) return true;
  if (s.indexOf("GALAXY") >= 0) return true;
  if (s.indexOf("HUAWEI") >= 0) return true;
  if (s.indexOf("POCO") >= 0) return true;

  return false;
}

// =================  SEND =================

void sendCmd(String cmd) {
  Serial.print("CMD> ");
  Serial.println(cmd);
  LoRa.println(cmd);
}



void sendJson(String url, String json) {
  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  int code = http.POST(json);
  Serial.print("POST to ");
  Serial.print(url);
  Serial.print(" -> HTTP STATUS: ");
  Serial.println(code);

  if (code > 0) {
    Serial.println("Server response:");
    Serial.println(http.getString());
  }

  http.end();
}

// ================= LORA UTILS =================
void sendCmd(String cmd) {
  Serial.print("CMD> ");
  Serial.println(cmd);
  LoRa.println(cmd);
}

void pollLoRa(unsigned long timeoutMs = 200) {
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    if (!LoRa.available()) {
      delay(5);
      continue;
    }

    String line = LoRa.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;

    Serial.print("LoRa received: ");
    Serial.println(line);

    if (line.indexOf("+JOIN: Success") >= 0 ||
        line.indexOf("Network joined") >= 0) {
      isJoined = true;
      Serial.println("TTN Network joined successfully!");
    }
  }
}


// Join TTN via OTAA
void joinTTN() {
  sendCmd("AT");
  pollLoRa();

  sendCmd("AT+DR=EU868");
  pollLoRa();

  sendCmd("AT+MODE=LWOTAA");
  pollLoRa();

  sendCmd("AT+CLASS=A");
  pollLoRa();

  sendCmd("AT+PORT=2");
  pollLoRa();

  sendCmd("AT+ID=DevEui," + String(DEVEUI));
  pollLoRa();

  sendCmd("AT+ID=AppEui," + String(APPEUI));
  pollLoRa();

  sendCmd("AT+KEY=APPKEY," + String(APPKEY));
  pollLoRa();

  Serial.println("Sending join request...");
  sendCmd("AT+JOIN");
  pollLoRa(3000); // join can take time
}


int packScanData(uint8_t* buffer, int maxLen) {
  int index = 0;

  // Device ID as a single byte (map "esp32-001" -> 1)
  buffer[index++] = 1;

  int n = WiFi.scanNetworks(false, true);
  int validCount = 0;
  buffer[index++] = 0; // placeholder for scan count

  for (int i = 0; i < n && validCount < 7; i++) {
    String ssid = WiFi.SSID(i);
    uint8_t* bssid = WiFi.BSSID(i);
    if (isLikelyHotspot(ssid, bssid)) continue;

    // MAC 6 bytes
    for (int m = 0; m < 6; m++) buffer[index++] = bssid[m];

    // RSSI 1 byte (signed)
    int8_t rssi = WiFi.RSSI(i);
    buffer[index++] = rssi;

    validCount++;
  }

  // Set scan count
  buffer[1] = validCount;

  return index; // total length of payload
}




// ================= SETUP =================
void setup() {
  Serial.begin(115200);
  delay(1000);

  // Wi-Fi setup
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("WiFi CONNECTED!");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi FAILED TO CONNECT");
  }

  // LoRa setup (optional)
  LoRa.begin(LORA_BAUD, SERIAL_8N1, LORA_RX, LORA_TX);
  Serial.println("LoRa initialized");

  joinTTN();
}

// ================= LOOP =================
void loop() {
  // -------- 1. Check LoRa for messages and join status --------
  while (LoRa.available()) {
    String line = LoRa.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;

    Serial.print("LoRa received: ");
    Serial.println(line);

    if (line.indexOf("+JOIN: Success") >= 0 ||
        line.indexOf("Network joined") >= 0) {
      isJoined = true;
      Serial.println("TTN Network joined successfully!");
    }
  }

  // -------- 2. Wi-Fi must be connected --------
  if (WiFi.status() != WL_CONNECTED) return;

  // -------- 3. Send Wi-Fi scan every interval --------
  long now = millis();
  if (now - lastSendTime < sendInterval) return;
  lastSendTime = now;

  Serial.println("\n--- WiFi Scan ---");
  int n = WiFi.scanNetworks(false, true);
  if (n <= 0) {
    Serial.println("No networks found");
    return;
  }

  // -------- 4. Dual-mode send --------
  if (isJoined) {
    // ===== LoRaWAN send =====
    uint8_t payload[51];
    int len = packScanData(payload, 51);

    Serial.print("--- Sending payload over TTN, length: ");
    Serial.println(len);

    // Optional: print payload for debug
    Serial.print("Payload bytes: ");
    for (int i = 0; i < len; i++) {
      Serial.print(payload[i], HEX);
      Serial.print(" ");
    }
    Serial.println();

    LoRa.write(payload, len);
    sendCmd("AT+SEND=2," + String(len));
  } else {
    // ===== Fallback HTTP send =====
    String scanJson = "[";
    int validCount = 0;

    for (int i = 0; i < n && validCount < MAX_AP_TO_SEND; i++) {
      String ssid = WiFi.SSID(i);
      uint8_t* bssid = WiFi.BSSID(i);
      if (isLikelyHotspot(ssid, bssid)) continue;

      if (validCount > 0) scanJson += ",";

      char mac[18];
      sprintf(mac, "%02X:%02X:%02X:%02X:%02X:%02X",
              bssid[0], bssid[1], bssid[2],
              bssid[3], bssid[4], bssid[5]);

      scanJson += "{\"mac\":\"";
      scanJson += mac;
      scanJson += "\",\"rssi\":";
      scanJson += WiFi.RSSI(i);
      scanJson += "}";

      Serial.print(validCount);
      Serial.print(": ");
      Serial.print(ssid);
      Serial.print(" (");
      Serial.print(WiFi.RSSI(i));
      Serial.print(" dBm) MAC: ");
      Serial.println(mac);

      validCount++;
    }

    scanJson += "]";

    if (validCount == 0) {
      Serial.println("No valid fixed APs to send");
      return;
    }

    // Build JSON payload
    String locatePayload = "{";
    locatePayload += "\"device_id\":\"" DEVICE_ID "\",";
    locatePayload += "\"scan\":" + scanJson;
    locatePayload += "}";

    Serial.println("--- Sending fallback HTTP payload ---");
    Serial.println(locatePayload);
    sendJson(LOCATE_URL, locatePayload);
  }
}
