import requests
import json

# Optional: for GPS reading (Linux)
try:
    import gpsd
    GPS_AVAILABLE = True
except ImportError:
    GPS_AVAILABLE = False

# -----------------------------
# CONFIG
# -----------------------------
DEVICE_ID = "esp32-001"  # Change to your ESP32 device ID
CALIBRATE_URL = "http://vps-98cd652a.vps.ovh.net:8067/scan/calibrate"

def get_gps_coordinates():
    """Try to get coordinates from GPS; fall back to manual input."""
    if GPS_AVAILABLE:
        try:
            gpsd.connect()  # connect to local gpsd
            packet = gpsd.get_current()
            lat = packet.lat
            lon = packet.lon
            if lat is not None and lon is not None:
                print(f"GPS coordinates obtained: {lat}, {lon}")
                return lat, lon
        except Exception as e:
            print(f"GPS error: {e}")

    # Manual fallback
    lat = float(input("Enter latitude: "))
    lon = float(input("Enter longitude: "))
    return lat, lon

def send_calibration(device_id, lat, lon):
    payload = {
        "device_id": device_id,
        "lat": lat,
        "lon": lon
    }

    headers = {"Content-Type": "application/json"}
    try:
        print(f"Sending calibration payload: {json.dumps(payload)}")
        response = requests.post(CALIBRATE_URL, json=payload, headers=headers)
        if response.status_code == 200:
            print("Server response:", response.json())
        else:
            print(f"Error {response.status_code}: {response.text}")
    except requests.RequestException as e:
        print("Request failed:", e)

if __name__ == "__main__":
    lat, lon = get_gps_coordinates()
    send_calibration(DEVICE_ID, lat, lon)
