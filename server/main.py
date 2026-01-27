from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Tuple
from math import sqrt
from sqlmodel import SQLModel, Field, Session, create_engine, select
import folium

from time import time

lora_buffers = {}  # device_id -> buffer
LORA_TIMEOUT = 15  # seconds


# -----------------------------
# Database setup (SQLite)
# -----------------------------
DATABASE_FILE = "fingerprints.db"
engine = create_engine(f"sqlite:///{DATABASE_FILE}", echo=False)

# -----------------------------
# Models
# -----------------------------
class Fingerprint(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    lat: float
    lon: float

class FingerprintEntry(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    fingerprint_id: int = Field(foreign_key="fingerprint.id")
    mac: str
    rssi: int

# Create tables if not exist
SQLModel.metadata.create_all(engine)

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="Wi-Fi Fingerprinting Server with Map")

# -----------------------------
# Pydantic models
# -----------------------------
class WifiReading(BaseModel):
    mac: str
    rssi: int

class WifiScan(BaseModel):
    device_id: str
    scan: List[WifiReading]

class CalibrationRequest(BaseModel):
    device_id: str
    lat: float
    lon: float

# -----------------------------
# Dependency: session generator
# -----------------------------
def get_session():
    with Session(engine) as session:
        yield session

# -----------------------------
# Pending calibration storage
# -----------------------------
# Store device_id -> coordinates until next scan arrives
pending_calibrations: Dict[str, Tuple[float, float]] = {}

# Store last estimated position per device
last_estimated_positions: Dict[str, Tuple[float, float]] = {}


# -----------------------------
# Calibration endpoint
# -----------------------------
@app.post("/scan/calibrate")
def calibrate(req: CalibrationRequest):
    """
    Receive device_id + coordinates and store them in memory.
    The next /scan/locate payload from this device will be saved in the database.
    """
    pending_calibrations[req.device_id] = (req.lat, req.lon)
    return {
        "status": "pending",
        "device_id": req.device_id,
        "lat": req.lat,
        "lon": req.lon,
        "message": "Next Wi-Fi scan from this device will be linked to these coordinates"
    }



# -----------------------------
# Locate endpoint
# -----------------------------
@app.post("/scan/locate")
def locate(scan: WifiScan, session: Session = Depends(get_session)):
    """
    Compare live scan against stored fingerprints and estimate position.
    If calibration pending for this device, save the fingerprint first.
    """
    scan_fp = {ap.mac: ap.rssi for ap in scan.scan}

    # ----------- Store fingerprint if calibration is pending -----------
    if scan.device_id in pending_calibrations:
        lat, lon = pending_calibrations.pop(scan.device_id)
        fingerprint = Fingerprint(lat=lat, lon=lon)
        session.add(fingerprint)
        session.commit()
        session.refresh(fingerprint)

        for ap in scan.scan:
            entry = FingerprintEntry(
                fingerprint_id=fingerprint.id,
                mac=ap.mac,
                rssi=ap.rssi
            )
            session.add(entry)
        session.commit()
        print(f"[INFO] Calibration saved for device {scan.device_id} at ({lat}, {lon})")

    # ----------- Estimate position -----------
    fingerprints = session.exec(select(Fingerprint)).all()
    if not fingerprints:
        return {
            "status": "pending",
            "message": "No fingerprints in database. Calibrate first."
        }

    weighted_lat = 0.0
    weighted_lon = 0.0
    total_weight = 0.0

    for fp in fingerprints:
        entries = session.exec(
            select(FingerprintEntry).where(FingerprintEntry.fingerprint_id == fp.id)
        ).all()
        db_fp = {e.mac: e.rssi for e in entries}

        # Only consider common MACs
        common_macs = set(scan_fp.keys()) & set(db_fp.keys())
        if not common_macs:
            continue

        # Euclidean distance
        distance = sqrt(sum((scan_fp[mac] - db_fp[mac]) ** 2 for mac in common_macs))
        weight = 1 / (distance + 0.01)  # avoid division by 0

        weighted_lat += weight * fp.lat
        weighted_lon += weight * fp.lon
        total_weight += weight

    if total_weight == 0:
        return {
            "status": "pending",
            "message": "No matching fingerprints found. Send Wi-Fi scan after calibration."
        }

    estimated_lat = weighted_lat / total_weight
    estimated_lon = weighted_lon / total_weight

    # Save last estimated position for this device
    last_estimated_positions[scan.device_id] = (estimated_lat, estimated_lon)

    return {
        "status": "ok",
        "estimated_position": {"lat": estimated_lat, "lon": estimated_lon}
    }

# -----------------------------
# Map endpoint
# -----------------------------
@app.get("/map/{device_id}", response_class=HTMLResponse)
def show_map(device_id: str, session: Session = Depends(get_session)):

    # If we have an estimated position → use it
    if device_id in last_estimated_positions:
        lat, lon = last_estimated_positions[device_id]
        center_lat, center_lon = lat, lon
        marker_label = "Estimated position"
        marker_color = "red"

    # Else fallback to last calibration point
    else:
        latest_fp = session.exec(
            select(Fingerprint).order_by(Fingerprint.id.desc())
        ).first()

        if latest_fp:
            center_lat = latest_fp.lat
            center_lon = latest_fp.lon
            marker_label = "Calibration point"
            marker_color = "blue"
        else:
            center_lat = 48.8566
            center_lon = 2.3522
            marker_label = "Default"
            marker_color = "gray"

    m = folium.Map(location=[center_lat, center_lon], zoom_start=17)

    folium.Marker(
        [center_lat, center_lon],
        tooltip=f"Device: {device_id}",
        popup=f"{marker_label}<br>Lat: {center_lat}<br>Lon: {center_lon}",
        icon=folium.Icon(color=marker_color, icon="info-sign")
    ).add_to(m)

    html = m._repr_html_()

    auto_refresh = """
    <script>
    setTimeout(function(){
        window.location.reload();
    }, 3000);
    </script>
    """
    
    return html.replace("</body>", auto_refresh + "</body>")
    


# -----------------------------
# Root endpoint
# -----------------------------
@app.get("/")
def root():
    return {"message": "Wi-Fi Fingerprinting Server with SQLite Online"}
