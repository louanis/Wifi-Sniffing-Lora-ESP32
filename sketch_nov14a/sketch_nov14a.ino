#include <WiFi.h>
#include "SPIFFS.h"
#include <FS.h>
#include <ArduinoJson.h>

// ----------------- New: CSV / location storage -----------------
const char* CSV_PATH = "/localBDD.csv";
String currentLocation = "unknown_location";

void ensureCSVHeader() {
  if (!SPIFFS.exists(CSV_PATH)) {
    File f = SPIFFS.open(CSV_PATH, FILE_WRITE);
    if (f) {
      // Header: timestamp(ms),location,kept,ssid,bssid,rssi,channel
      f.println("timestamp_ms,location,kept,ssid,bssid,rssi,channel");
      f.close();
    }
  }
}

String macToString(const uint8_t* mac) {
  char buf[18];
  sprintf(buf, "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(buf);
}

void appendNetworkCSV(unsigned long ts, const String& location, bool kept, const String& ssid, const uint8_t* mac, int rssi, int channel) {
  File f = SPIFFS.open(CSV_PATH, FILE_APPEND);
  if (!f) return;
  String line = "";
  line += String(ts);
  line += ",";
  // escape commas in location/ssid minimally by wrapping quotes if needed
  if (location.indexOf(',') >= 0 || location.indexOf('"') >= 0) {
    line += "\"" + location + "\"";
  } else {
    line += location;
  }
  line += ",";
  line += (kept ? "1" : "0");
  line += ",";
  if (ssid.indexOf(',') >= 0 || ssid.indexOf('"') >= 0) {
    line += "\"" + ssid + "\"";
  } else {
    line += ssid;
  }
  line += ",";
  line += macToString(mac);
  line += ",";
  line += String(rssi);
  line += ",";
  line += String(channel);
  f.println(line);
  f.close();
}

// Parse serial commands: "loc <label>" sets currentLocation
void handleSerialCommands() {
  if (!Serial.available()) return;
  String s = Serial.readStringUntil('\n');
  s.trim();
  if (s.length() == 0) return;
  if (s.startsWith("loc ")) {
    String loc = s.substring(4);
    loc.trim();
    if (loc.length() > 0) {
      currentLocation = loc;
      Serial.print("Location set to: ");
      Serial.println(currentLocation);
    }
  } else if (s == "showcsv") {
    // dump CSV to serial (small helper)
    if (SPIFFS.exists(CSV_PATH)) {
      File f = SPIFFS.open(CSV_PATH, FILE_READ);
      if (f) {
        Serial.println("=== CSV DUMP ===");
        while (f.available()) {
          Serial.write(f.read());
        }
        Serial.println("\n=== END CSV ===");
        f.close();
      }
    } else {
      Serial.println("CSV file not found.");
    }
  } else {
    Serial.println("Unknown command. Use: loc <label>  or  showcsv");
  }
}
// ----------------- End CSV / location code -----------------

void setup() {
  Serial.begin(115200);
  delay(100);

  // Initialize SPIFFS
  if (!SPIFFS.begin(true)) {
    Serial.println("SPIFFS mount failed");
  } else {
    ensureCSVHeader();
  }

  Serial.println("\n\nWiFi Localization Filter System");
  Serial.println("Use 'loc <label>' to set current location (e.g. loc floor1_roomA)");
  Serial.println("Use 'showcsv' to dump saved CSV");

  // WiFi setup
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);
}

void loop() {
  handleSerialCommands();

  Serial.println("\nScanning...");

  int n = WiFi.scanNetworks(false, true);  // scan, include hidden networks
  if (n <= 0) {
    Serial.println("No networks found.");
    delay(5000);
    return;
  }

  // Prepare JSON document
  StaticJsonDocument<4096> doc;
  JsonArray networks = doc.createNestedArray("networks");
  doc["timestamp"] = millis();

  Serial.printf("%d networks found\n", n);

  for (int i = 0; i < n; i++) {
    JsonObject ap = networks.createNestedObject();
    ap["ssid"] = WiFi.SSID(i);
    ap["bssid"] = WiFi.BSSIDstr(i);
    ap["rssi"] = WiFi.RSSI(i);
    ap["channel"] = WiFi.channel(i);
    ap["encryption"] = WiFi.encryptionType(i);

    // Debug print
    Serial.printf(
      "SSID: %s | BSSID: %s | RSSI: %d | CH: %d | ENC: %d\n",
      WiFi.SSID(i).c_str(),
      WiFi.BSSIDstr(i).c_str(),
      WiFi.RSSI(i),
      WiFi.channel(i),
      WiFi.encryptionType(i)
    );

    // Save every detected AP to CSV with kept flag (kept determined below)
    bool kept = true; // Placeholder for actual filtering logic
    appendNetworkCSV(millis(), currentLocation, kept, WiFi.SSID(i), WiFi.BSSID(i), WiFi.RSSI(i), WiFi.channel(i));
  }

  // Print JSON output
  String jsonOut;
  serializeJson(doc, jsonOut);
  Serial.println("\nJSON Output:");
  Serial.println(jsonOut);

  // Wait 5 seconds before next scan
  delay(5000);
}
