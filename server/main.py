from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
from math import sqrt
from sqlmodel import SQLModel, Field, Session, create_engine, select
import folium

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

# Create tables
SQLModel.metadata.create_all(engine)

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="Wi-Fi Fingerprinting Server with Map")

# -----------------------------
# Pydantic models for API
# -----------------------------
class WifiReading(BaseModel):
    mac: str
    rssi: int

class WifiScan(BaseModel):
    device_id: str
    scan: List[WifiReading]

# -----------------------------
# Dependency: session generator
# -----------------------------
def get_session():
    with Session(engine) as session:
        yield session

# -----------------------------
# Calibration endpoint
# -----------------------------
@app.post("/scan/calibrate")
def calibrate(scan: WifiScan, lat: float, lon: float, session: Session = Depends(get_session)):
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

    total = session.exec(select(Fingerprint)).count()
    return {
        "status": "stored",
        "fingerprint_id": fingerprint.id,
        "total_fingerprints": total
    }

# -----------------------------
# Localization endpoint
# -----------------------------
@app.post("/scan/locate")
def locate(scan: WifiScan, session: Session = Depends(get_session)):
    """
    Compare live scan against stored fingerprints and estimate position
    using weighted average of RSSI similarities.
    """
    scan_fp = {ap.mac: ap.rssi for ap in scan.scan}

    # Load all fingerprints
    fingerprints = session.exec(select(Fingerprint)).all()
    if not fingerprints:
        raise HTTPException(status_code=404, detail="No fingerprints in database")

    best_score = float("inf")
    best_position = None

    weights = []  # To store weights for each fingerprint
    weighted_lat = 0.0
    weighted_lon = 0.0
    total_weight = 0.0

    for fp in fingerprints:
        # Get the fingerprint entries for this position
        entries = session.exec(
            select(FingerprintEntry).where(FingerprintEntry.fingerprint_id == fp.id)
        ).all()

        db_fp = {e.mac: e.rssi for e in entries}

        # Find common MACs between scan and stored fingerprint
        common_macs = set(scan_fp.keys()) & set(db_fp.keys())
        if not common_macs:
            continue

        # Euclidean distance in RSSI space
        distance = sqrt(
            sum((scan_fp[mac] - db_fp[mac]) ** 2 for mac in common_macs)
        )

        # Compute the weight as the inverse of the distance
        weight = 1 / (distance + 0.01)  # Adding a small value to avoid division by 0

        # Update weighted position
        weighted_lat += weight * fp.lat
        weighted_lon += weight * fp.lon
        total_weight += weight

    if total_weight == 0:
        raise HTTPException(status_code=404, detail="No matching fingerprints found")

    # Compute the final weighted average position
    estimated_lat = weighted_lat / total_weight
    estimated_lon = weighted_lon / total_weight

    return {
        "status": "ok",
        "estimated_position": {"lat": estimated_lat, "lon": estimated_lon},
        "score": best_score
    }

    
# -----------------------------
# Map endpoint
# -----------------------------
@app.get("/map/{device_id}", response_class=HTMLResponse)
def show_map(device_id: str, session: Session = Depends(get_session)):
    latest_fp = session.exec(
        select(Fingerprint).order_by(Fingerprint.id.desc())
    ).first()

    if latest_fp:
        center_lat = latest_fp.lat
        center_lon = latest_fp.lon
    else:
        center_lat = 48.8566  # default center (Paris)
        center_lon = 2.3522

    m = folium.Map(location=[center_lat, center_lon], zoom_start=16)

    if latest_fp:
        folium.Marker(
            [latest_fp.lat, latest_fp.lon],
            tooltip=f"Device: {device_id}",
            popup=f"Lat: {latest_fp.lat}, Lon: {latest_fp.lon}",
            icon=folium.Icon(color="red", icon="info-sign")
        ).add_to(m)

    return m._repr_html_()

# -----------------------------
# Root endpoint
# -----------------------------
@app.get("/")
def root():
    return {"message": "Wi-Fi Fingerprinting Server with SQLite Online"}
