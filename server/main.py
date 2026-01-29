from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Tuple
from math import sqrt
from sqlmodel import SQLModel, Field, Session, create_engine, select
import folium

# -----------------------------
# Database setup
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

SQLModel.metadata.create_all(engine)

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="Wi-Fi Fingerprinting Server")

# -----------------------------
# Internal normalized models
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
# State
# -----------------------------
pending_calibrations: Dict[str, Tuple[float, float]] = {}
last_estimated_positions: Dict[str, Tuple[float, float]] = {}

# -----------------------------
# DB session
# -----------------------------
def get_session():
    with Session(engine) as session:
        yield session

# -----------------------------
# Payload normalizer (THE FIX)
# -----------------------------
def extract_wifi_scan(payload: dict) -> WifiScan:
    """
    Accept decoded-only payload OR full TTN webhook payload.
    """

    # Case 1: already decoded payload
    if isinstance(payload, dict) and "device_id" in payload and "scan" in payload:
        return WifiScan(**payload)

    decoded = None

    # Case 2: TTN standard webhook
    decoded = (
        payload.get("data", {})
               .get("uplink_message", {})
               .get("decoded_payload")
    )

    # Case 3: alternate TTN layout
    if decoded is None:
        decoded = (
            payload.get("uplink_message", {})
                   .get("decoded_payload")
        )

    # Case 4: last resort
    if decoded is None:
        decoded = payload.get("decoded_payload")

    # Validate decoded payload
    if not isinstance(decoded, dict):
        raise HTTPException(
            status_code=400,
            detail="No decoded_payload found in TTN webhook"
        )

    if "device_id" not in decoded or "scan" not in decoded:
        raise HTTPException(
            status_code=400,
            detail="decoded_payload missing device_id or scan"
        )

    return WifiScan(**decoded)

# -----------------------------
# Calibration endpoint
# -----------------------------
@app.post("/scan/calibrate")
async def calibrate(request: Request):
    payload = await request.json()

    try:
        req = CalibrationRequest(**payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid calibration payload")

    pending_calibrations[req.device_id] = (req.lat, req.lon)

    return {
        "status": "pending",
        "device_id": req.device_id,
        "lat": req.lat,
        "lon": req.lon
    }

# -----------------------------
# Locate endpoint
# -----------------------------
@app.post("/scan/locate")
async def locate(request: Request, session: Session = Depends(get_session)):
    payload = await request.json()
    scan = extract_wifi_scan(payload)

    scan_fp = {ap.mac: ap.rssi for ap in scan.scan}

    # ----- Calibration save -----
    if scan.device_id in pending_calibrations:
        lat, lon = pending_calibrations.pop(scan.device_id)
        fp = Fingerprint(lat=lat, lon=lon)
        session.add(fp)
        session.commit()
        session.refresh(fp)

        for ap in scan.scan:
            session.add(FingerprintEntry(
                fingerprint_id=fp.id,
                mac=ap.mac,
                rssi=ap.rssi
            ))
        session.commit()

    # ----- Position estimation -----
    fingerprints = session.exec(select(Fingerprint)).all()
    if not fingerprints:
        return {"status": "pending", "message": "No fingerprints yet"}

    weighted_lat = weighted_lon = total_weight = 0.0

    for fp in fingerprints:
        entries = session.exec(
            select(FingerprintEntry).where(
                FingerprintEntry.fingerprint_id == fp.id
            )
        ).all()

        db_fp = {e.mac: e.rssi for e in entries}
        common = set(scan_fp) & set(db_fp)
        if not common:
            continue

        dist = sqrt(sum((scan_fp[m] - db_fp[m]) ** 2 for m in common))
        weight = 1 / (dist + 0.01)

        weighted_lat += weight * fp.lat
        weighted_lon += weight * fp.lon
        total_weight += weight

    if total_weight == 0:
        return {"status": "pending", "message": "No matching fingerprints"}

    lat = weighted_lat / total_weight
    lon = weighted_lon / total_weight
    
    # Save last estimated position
    last_estimated_positions[scan.device_id] = (lat, lon)
    
    # ---- ADD THIS PART ----
    position_history.setdefault(scan.device_id, []).append((lat, lon))
    
    # keep last ~4 minutes of data (50 points @ 5s)
    position_history[scan.device_id] = position_history[scan.device_id][-50:]
    # -----------------------
    

    return {
        "status": "ok",
        "estimated_position": {"lat": lat, "lon": lon}
    }

# -----------------------------
# Map endpoint
# -----------------------------
@app.get("/map/{device_id}", response_class=HTMLResponse)
def show_map(device_id: str, session: Session = Depends(get_session)):

    history = position_history.get(device_id)

    # Fallback if nothing yet
    if not history:
        fp = session.exec(
            select(Fingerprint).order_by(Fingerprint.id.desc())
        ).first()

        if fp:
            lat, lon = fp.lat, fp.lon
        else:
            lat, lon = 48.8566, 2.3522

        m = folium.Map(location=[lat, lon], zoom_start=17)
        return HTMLResponse(content=m.get_root().render())

    # Center on latest point
    lat, lon = history[-1]
    m = folium.Map(location=[lat, lon], zoom_start=17)

    total = len(history)

    # Draw fading trail
    for i in range(1, total):
        lat1, lon1 = history[i - 1]
        lat2, lon2 = history[i]

        # 0.0 (oldest) → 1.0 (newest)
        ratio = i / total

        intensity = int(255 * ratio)
        intensity = max(60, intensity)  # avoid too dark

        color = f"#{intensity:02x}0000"  # dark → bright red

        folium.PolyLine(
            locations=[(lat1, lon1), (lat2, lon2)],
            color=color,
            weight=4,
            opacity=0.9
        ).add_to(m)

    html_str = m.get_root().render()

    # Auto-refresh every 5 seconds (match uplink rate)
    refresh_tag = '<meta http-equiv="refresh" content="5">'
    html_str = html_str.replace("<head>", f"<head>{refresh_tag}")

    return HTMLResponse(content=html_str)


# -----------------------------
# Root
# -----------------------------
@app.get("/")
def root():
    return {"status": "online"}
