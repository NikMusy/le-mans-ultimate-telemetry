"""
LMU PIT WALL — Telemetry Server
================================
Backend для Le Mans Ultimate (движок rFactor 2).

Читает Windows shared memory секции `$rFactor2SMMP_Telemetry$` и
`$rFactor2SMMP_Scoring$`, созданные плагином `rF2SharedMemoryMapPlugin64.dll`
(TheIronWolfMod), парсит их через ctypes и стримит снимок состояния по
WebSocket на 50 Гц.

INSTALLATION
------------
    pip install fastapi "uvicorn[standard]"

RUN
---
    python server.py                      # боевой режим (читает SHM)
    python server.py --demo               # синтетические данные (без LMU)
    python server.py --host 0.0.0.0 --port 8000 --hz 50

REMOTE ACCESS (стратег в другом городе)
---------------------------------------
    ngrok http 8000
    -> возьми HTTPS URL из терминала ngrok, отправь стратегу.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import json
import math
import mmap
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
import uvicorn

import f1_telemetry


# ============================================================
# Constants
# ============================================================

TELEMETRY_SHM = "$rFactor2SMMP_Telemetry$"
SCORING_SHM   = "$rFactor2SMMP_Scoring$"

MAX_MAPPED_VEHICLES = 128
KELVIN = 273.15

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_HZ   = 50

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


# ============================================================
# ctypes structures — rF2 SMMP plugin (pragma pack(4))
# ============================================================

class rF2Vec3(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("x", ctypes.c_double),
        ("y", ctypes.c_double),
        ("z", ctypes.c_double),
    ]


class rF2Wheel(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mSuspensionDeflection",    ctypes.c_double),
        ("mRideHeight",              ctypes.c_double),
        ("mSuspForce",               ctypes.c_double),
        ("mBrakeTemp",               ctypes.c_double),       # Kelvin
        ("mBrakePressure",           ctypes.c_double),

        ("mRotation",                ctypes.c_double),
        ("mLateralPatchVel",         ctypes.c_double),
        ("mLongitudinalPatchVel",    ctypes.c_double),
        ("mLateralGroundVel",        ctypes.c_double),
        ("mLongitudinalGroundVel",   ctypes.c_double),
        ("mCamber",                  ctypes.c_double),
        ("mLateralForce",            ctypes.c_double),
        ("mLongitudinalForce",       ctypes.c_double),
        ("mTireLoad",                ctypes.c_double),

        ("mGripFract",               ctypes.c_double),
        ("mPressure",                ctypes.c_double),       # kPa
        ("mTemperature",             ctypes.c_double * 3),   # inner/center/outer, Kelvin
        ("mWear",                    ctypes.c_double),       # 0..1, 1=unworn
        ("mTerrainName",             ctypes.c_char   * 16),
        ("mSurfaceType",             ctypes.c_ubyte),
        ("mFlat",                    ctypes.c_ubyte),
        ("mDetached",                ctypes.c_ubyte),
        ("mStaticUndeflectedRadius", ctypes.c_ubyte),

        ("mVerticalTireDeflection",  ctypes.c_double),
        ("mWheelYLocation",          ctypes.c_double),
        ("mToe",                     ctypes.c_double),

        ("mTireCarcassTemperature",      ctypes.c_double),    # Kelvin
        ("mTireInnerLayerTemperature",   ctypes.c_double * 3),

        ("mExpansion",               ctypes.c_ubyte * 24),
    ]


class rF2VehicleTelemetry(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mID",                     ctypes.c_int),
        ("mDeltaTime",              ctypes.c_double),
        ("mElapsedTime",            ctypes.c_double),
        ("mLapNumber",              ctypes.c_int),
        ("mLapStartET",             ctypes.c_double),
        ("mVehicleName",            ctypes.c_char * 64),
        ("mTrackName",              ctypes.c_char * 64),

        ("mPos",                    rF2Vec3),
        ("mLocalVel",               rF2Vec3),
        ("mLocalAccel",             rF2Vec3),

        ("mOri",                    rF2Vec3 * 3),
        ("mLocalRot",               rF2Vec3),
        ("mLocalRotAccel",          rF2Vec3),

        ("mGear",                   ctypes.c_int),
        ("mEngineRPM",              ctypes.c_double),
        ("mEngineWaterTemp",        ctypes.c_double),       # Celsius
        ("mEngineOilTemp",          ctypes.c_double),       # Celsius
        ("mClutchRPM",              ctypes.c_double),

        ("mUnfilteredThrottle",     ctypes.c_double),       # 0..1
        ("mUnfilteredBrake",        ctypes.c_double),       # 0..1
        ("mUnfilteredSteering",     ctypes.c_double),       # -1..1
        ("mUnfilteredClutch",       ctypes.c_double),

        ("mFilteredThrottle",       ctypes.c_double),
        ("mFilteredBrake",          ctypes.c_double),
        ("mFilteredSteering",       ctypes.c_double),
        ("mFilteredClutch",         ctypes.c_double),

        ("mSteeringShaftTorque",    ctypes.c_double),
        ("mFront3rdDeflection",     ctypes.c_double),
        ("mRear3rdDeflection",      ctypes.c_double),

        ("mFrontWingHeight",        ctypes.c_double),
        ("mFrontRideHeight",        ctypes.c_double),
        ("mRearRideHeight",         ctypes.c_double),
        ("mDrag",                   ctypes.c_double),
        ("mFrontDownforce",         ctypes.c_double),
        ("mRearDownforce",          ctypes.c_double),

        ("mFuel",                   ctypes.c_double),       # liters
        ("mEngineMaxRPM",           ctypes.c_double),
        ("mScheduledStops",         ctypes.c_ubyte),
        ("mOverheating",            ctypes.c_ubyte),
        ("mDetached",               ctypes.c_ubyte),
        ("mHeadlights",             ctypes.c_ubyte),
        ("mDentSeverity",           ctypes.c_ubyte * 8),
        ("mLastImpactET",           ctypes.c_double),
        ("mLastImpactMagnitude",    ctypes.c_double),
        ("mLastImpactPos",          rF2Vec3),

        ("mEngineTorque",           ctypes.c_double),
        ("mCurrentSector",          ctypes.c_int),
        ("mSpeedLimiter",           ctypes.c_ubyte),
        ("mMaxGears",               ctypes.c_ubyte),
        ("mFrontTireCompoundIndex", ctypes.c_ubyte),
        ("mRearTireCompoundIndex",  ctypes.c_ubyte),
        ("mFuelCapacity",           ctypes.c_double),
        ("mFrontFlapActivated",     ctypes.c_ubyte),
        ("mRearFlapActivated",      ctypes.c_ubyte),
        ("mRearFlapLegalStatus",    ctypes.c_ubyte),
        ("mIgnitionStarter",        ctypes.c_ubyte),

        ("mFrontTireCompoundName",  ctypes.c_char * 18),
        ("mRearTireCompoundName",   ctypes.c_char * 18),

        ("mSpeedLimiterAvailable",  ctypes.c_ubyte),
        ("mAntiStallActivated",     ctypes.c_ubyte),
        ("mUnused",                 ctypes.c_ubyte * 2),
        ("mVisualSteeringWheelRange", ctypes.c_float),

        ("mRearBrakeBias",          ctypes.c_double),
        ("mTurboBoostPressure",     ctypes.c_double),
        ("mPhysicsToGraphicsOffset", ctypes.c_float * 3),
        ("mPhysicalSteeringWheelRange", ctypes.c_float),

        ("mExpansion",              ctypes.c_ubyte * 152),

        ("mWheels",                 rF2Wheel * 4),  # FL, FR, RL, RR
    ]


class rF2Telemetry(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mVersionUpdateBegin",  ctypes.c_uint),
        ("mVersionUpdateEnd",    ctypes.c_uint),
        ("mBytesUpdatedHint",    ctypes.c_int),
        ("mNumVehicles",         ctypes.c_int),
        ("mVehicles",            rF2VehicleTelemetry * MAX_MAPPED_VEHICLES),
    ]


class rF2VehicleScoring(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mID",                  ctypes.c_int),
        ("mDriverName",          ctypes.c_char * 32),
        ("mVehicleName",         ctypes.c_char * 64),
        ("mTotalLaps",           ctypes.c_short),
        ("mSector",              ctypes.c_byte),     # 0..2
        ("mFinishStatus",        ctypes.c_byte),
        ("mLapDist",             ctypes.c_double),
        ("mPathLateral",         ctypes.c_double),
        ("mTrackEdge",           ctypes.c_double),

        ("mBestSector1",         ctypes.c_double),
        ("mBestSector2",         ctypes.c_double),
        ("mBestLapTime",         ctypes.c_double),
        ("mLastSector1",         ctypes.c_double),
        ("mLastSector2",         ctypes.c_double),
        ("mLastLapTime",         ctypes.c_double),
        ("mCurSector1",          ctypes.c_double),
        ("mCurSector2",          ctypes.c_double),

        ("mNumPitstops",         ctypes.c_short),
        ("mNumPenalties",        ctypes.c_short),
        ("mIsPlayer",            ctypes.c_ubyte),
        ("mControl",             ctypes.c_byte),
        ("mInPits",              ctypes.c_ubyte),
        ("mPlace",               ctypes.c_ubyte),
        ("mVehicleClass",        ctypes.c_char * 32),

        ("mTimeBehindNext",      ctypes.c_double),
        ("mLapsBehindNext",      ctypes.c_int),
        ("mTimeBehindLeader",    ctypes.c_double),
        ("mLapsBehindLeader",    ctypes.c_int),
        ("mLapStartET",          ctypes.c_double),

        ("mPos",                 rF2Vec3),
        ("mLocalVel",            rF2Vec3),
        ("mLocalAccel",          rF2Vec3),
        ("mOri",                 rF2Vec3 * 3),
        ("mLocalRot",            rF2Vec3),
        ("mLocalRotAccel",       rF2Vec3),

        ("mHeadlights",          ctypes.c_ubyte),
        ("mPitState",            ctypes.c_ubyte),
        ("mServerScored",        ctypes.c_ubyte),
        ("mIndividualPhase",     ctypes.c_ubyte),

        ("mQualification",       ctypes.c_int),
        ("mTimeIntoLap",         ctypes.c_double),
        ("mEstimatedLapTime",    ctypes.c_double),

        ("mPitGroup",            ctypes.c_char * 24),
        ("mFlag",                ctypes.c_ubyte),
        ("mUnderYellow",         ctypes.c_ubyte),
        ("mCountLapFlag",        ctypes.c_ubyte),
        ("mInGarageStall",       ctypes.c_ubyte),

        ("mUpgradePack",         ctypes.c_ubyte * 16),

        ("mPitLapDist",          ctypes.c_float),
        ("mBestLapSector1",      ctypes.c_float),
        ("mBestLapSector2",      ctypes.c_float),

        ("mExpansion",           ctypes.c_ubyte * 48),
    ]


class rF2ScoringInfo(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mTrackName",           ctypes.c_char * 64),
        ("mSession",             ctypes.c_int),
        ("mCurrentET",           ctypes.c_double),
        ("mEndET",               ctypes.c_double),
        ("mMaxLaps",             ctypes.c_int),
        ("mLapDist",             ctypes.c_double),
        # In source struct this is `char *mResultsStream` — a pointer.
        # SMMP plugin maps it as an 8-byte placeholder on x64.
        ("mResultsStreamPtr",    ctypes.c_ubyte * 8),
        ("mNumVehicles",         ctypes.c_int),
        ("mGamePhase",           ctypes.c_ubyte),
        ("mYellowFlagState",     ctypes.c_byte),
        ("mSectorFlag",          ctypes.c_byte * 3),
        ("mStartLight",          ctypes.c_ubyte),
        ("mNumRedLights",        ctypes.c_ubyte),
        ("mInRealtime",          ctypes.c_ubyte),
        ("mPlayerName",          ctypes.c_char * 32),
        ("mPlrFileName",         ctypes.c_char * 64),

        ("mDarkCloud",           ctypes.c_double),
        ("mRaining",             ctypes.c_double),
        ("mAmbientTemp",         ctypes.c_double),
        ("mTrackTemp",           ctypes.c_double),
        ("mWind",                rF2Vec3),
        ("mMinPathWetness",      ctypes.c_double),
        ("mMaxPathWetness",      ctypes.c_double),

        ("mGameMode",            ctypes.c_ubyte),
        ("mIsPasswordProtected", ctypes.c_ubyte),
        ("mServerPort",          ctypes.c_ushort),
        ("mServerPublicIP",      ctypes.c_uint),
        ("mMaxPlayers",          ctypes.c_int),
        ("mServerName",          ctypes.c_char * 32),
        ("mStartET",             ctypes.c_float),
        ("mAvgPathWetness",      ctypes.c_double),

        ("mExpansion",           ctypes.c_ubyte * 200),
        # Official header: a single x64 pointer placeholder (pointer2[8]).
        # The mid-struct results-stream pointer is mResultsStreamPtr above.
        # This was previously (wrongly) two 64-byte arrays, which pushed
        # mVehicles[] 120 bytes forward and corrupted every per-car field.
        ("mPointer2",            ctypes.c_ubyte * 8),
    ]


class rF2Scoring(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mVersionUpdateBegin",  ctypes.c_uint),
        ("mVersionUpdateEnd",    ctypes.c_uint),
        ("mBytesUpdatedHint",    ctypes.c_int),
        ("mScoringInfo",         rF2ScoringInfo),
        ("mVehicles",            rF2VehicleScoring * MAX_MAPPED_VEHICLES),
    ]


# ============================================================
# Shared memory reader
# ============================================================

class SharedMemoryReader:
    """Opens (or attaches to) a Windows page-file backed shared memory
    section by tagname and reads ctypes structs with a double-version
    check to avoid torn reads."""

    def __init__(self, name: str, struct_type):
        self.name = name
        self.struct_type = struct_type
        self.size = ctypes.sizeof(struct_type)
        self.mm: Optional[mmap.mmap] = None
        self._announced = False

    def _open(self) -> bool:
        try:
            self.mm = mmap.mmap(-1, self.size, tagname=self.name,
                                access=mmap.ACCESS_READ)
            if not self._announced:
                print(f"  [SHM] attached: {self.name}  ({self.size} bytes)",
                      flush=True)
                self._announced = True
            return True
        except Exception as exc:
            self.mm = None
            if not self._announced:
                print(f"  [SHM] open failed for {self.name}: {exc}",
                      flush=True)
            return False

    def read(self):
        if self.mm is None and not self._open():
            return None
        last = None
        try:
            for _ in range(5):
                self.mm.seek(0)
                buf = self.mm.read(self.size)
                last = self.struct_type.from_buffer_copy(buf)
                if last.mVersionUpdateBegin == last.mVersionUpdateEnd:
                    return last
            # 5 torn reads in a row — return last anyway
            return last
        except Exception as exc:
            print(f"  [SHM] read error ({self.name}): {exc}", flush=True)
            self.close()
            return None

    def close(self):
        if self.mm is not None:
            try:
                self.mm.close()
            except Exception:
                pass
            self.mm = None
            self._announced = False


# ============================================================
# Helpers
# ============================================================

def _bytes_to_str(b) -> str:
    if isinstance(b, bytes):
        return b.decode("latin-1", errors="ignore").rstrip("\x00").strip()
    return str(b)


def _find_player(scoring, telemetry) -> Tuple[int, Optional[rF2VehicleScoring]]:
    """Locate the player's vehicle index in `telemetry.mVehicles` and the
    matching scoring record."""
    if scoring is None:
        return 0, None

    player_id, player_scoring = None, None
    n = min(scoring.mScoringInfo.mNumVehicles, MAX_MAPPED_VEHICLES)
    for i in range(n):
        v = scoring.mVehicles[i]
        if v.mIsPlayer:
            player_id = v.mID
            player_scoring = v
            break
    if player_id is None:
        return 0, None
    if telemetry is None:
        return 0, player_scoring
    n = min(telemetry.mNumVehicles, MAX_MAPPED_VEHICLES)
    for i in range(n):
        if telemetry.mVehicles[i].mID == player_id:
            return i, player_scoring
    return 0, player_scoring


def _wheel_to_dict(w: rF2Wheel) -> dict:
    temps_c = [t - KELVIN for t in w.mTemperature]
    return {
        "temp_inner":  temps_c[0],
        "temp_center": temps_c[1],
        "temp_outer":  temps_c[2],
        "temp_avg":    sum(temps_c) / 3.0,
        "pressure":    float(w.mPressure),
        "wear":        float(w.mWear),
        "brake_temp":  float(w.mBrakeTemp) - KELVIN,
        "carcass_temp": float(w.mTireCarcassTemperature) - KELVIN,
    }


def build_snapshot(telemetry, scoring) -> dict:
    """Compose a single frame of the dashboard state."""
    if telemetry is None:
        return {"status": "waiting", "reason": "telemetry SHM not available"}
    if telemetry.mNumVehicles <= 0:
        return {"status": "waiting", "reason": "no active session"}

    idx, ps = _find_player(scoring, telemetry)
    v = telemetry.mVehicles[idx]

    speed_ms = math.sqrt(v.mLocalVel.x ** 2 + v.mLocalVel.y ** 2 + v.mLocalVel.z ** 2)

    out = {
        "status":        "live",
        "track":         _bytes_to_str(v.mTrackName),
        "vehicle":       _bytes_to_str(v.mVehicleName),
        "elapsed_time":  float(v.mElapsedTime),
        "lap_number":    int(v.mLapNumber),
        "lap_start_et":  float(v.mLapStartET),

        "speed_kmh":     speed_ms * 3.6,
        "gear":          int(v.mGear),
        "rpm":           float(v.mEngineRPM),
        "max_rpm":       float(v.mEngineMaxRPM) if v.mEngineMaxRPM > 0 else 10500.0,

        "fuel":          float(v.mFuel),
        "fuel_capacity": float(v.mFuelCapacity) if v.mFuelCapacity > 0 else None,
        "water_temp":    float(v.mEngineWaterTemp),
        "oil_temp":      float(v.mEngineOilTemp),
        "turbo_boost":   float(v.mTurboBoostPressure) / 100000.0,  # Pa -> bar (absolute)
        "engine_torque": float(v.mEngineTorque),

        "throttle":      max(0.0, min(1.0, float(v.mUnfilteredThrottle))),
        "brake":         max(0.0, min(1.0, float(v.mUnfilteredBrake))),
        "clutch":        max(0.0, min(1.0, float(v.mUnfilteredClutch))),
        "steering":      max(-1.0, min(1.0, float(v.mUnfilteredSteering))),

        "speed_limiter": bool(v.mSpeedLimiter),
        "ignition":      bool(v.mIgnitionStarter),
        "headlights":    bool(v.mHeadlights),
        "current_sector": int(v.mCurrentSector) + 1,

        "tire_compound_front": _bytes_to_str(v.mFrontTireCompoundName),
        "tire_compound_rear":  _bytes_to_str(v.mRearTireCompoundName),

        "tires": {
            "fl": _wheel_to_dict(v.mWheels[0]),
            "fr": _wheel_to_dict(v.mWheels[1]),
            "rl": _wheel_to_dict(v.mWheels[2]),
            "rr": _wheel_to_dict(v.mWheels[3]),
        },
    }

    if ps is not None:
        # Sector durations: SMMP exposes split times relative to lap start.
        last_s1 = ps.mLastSector1 if ps.mLastSector1 > 0 else None
        last_s2_split = ps.mLastSector2 if ps.mLastSector2 > 0 else None
        last_lap = ps.mLastLapTime if ps.mLastLapTime > 0 else None
        best_s1 = ps.mBestSector1 if ps.mBestSector1 > 0 else None
        best_s2_split = ps.mBestSector2 if ps.mBestSector2 > 0 else None
        best_lap = ps.mBestLapTime if ps.mBestLapTime > 0 else None
        cur_s1 = ps.mCurSector1 if ps.mCurSector1 > 0 else None
        cur_s2_split = ps.mCurSector2 if ps.mCurSector2 > 0 else None
        cur_sector_idx = (ps.mSector if ps.mSector >= 0 else 0)

        out["timing"] = {
            "place":          int(ps.mPlace),
            "total_laps":     int(ps.mTotalLaps),
            "current_sector": cur_sector_idx + 1,         # 1..3
            "in_pits":        bool(ps.mInPits),
            "in_garage":      bool(ps.mInGarageStall),
            "under_yellow":   bool(ps.mUnderYellow),
            "pit_state":      int(ps.mPitState),
            "num_pitstops":   int(ps.mNumPitstops),
            "num_penalties":  int(ps.mNumPenalties),
            "flag":           int(ps.mFlag),

            "time_into_lap":  float(ps.mTimeIntoLap) if ps.mTimeIntoLap > 0 else None,
            "estimated_lap":  float(ps.mEstimatedLapTime) if ps.mEstimatedLapTime > 0 else None,

            "last_lap":       last_lap,
            "best_lap":       best_lap,

            "cur_s1":         cur_s1,
            "cur_s2":         (cur_s2_split - cur_s1) if (cur_s1 and cur_s2_split) else None,
            "last_s1":        last_s1,
            "last_s2":        (last_s2_split - last_s1) if (last_s1 and last_s2_split) else None,
            "last_s3":        (last_lap - last_s2_split) if (last_lap and last_s2_split) else None,
            "best_s1":        best_s1,
            "best_s2":        (best_s2_split - best_s1) if (best_s1 and best_s2_split) else None,

            "behind_next":    float(ps.mTimeBehindNext),
            "behind_leader":  float(ps.mTimeBehindLeader),
            "laps_behind_leader": int(ps.mLapsBehindLeader),
            "driver":         _bytes_to_str(ps.mDriverName),
            "vehicle_class":  _bytes_to_str(ps.mVehicleClass),
        }

        si = scoring.mScoringInfo
        out["session"] = {
            "track_name":     _bytes_to_str(si.mTrackName),
            "current_et":     float(si.mCurrentET),
            "end_et":         float(si.mEndET),
            "max_laps":       int(si.mMaxLaps),
            "session":        int(si.mSession),
            "ambient_temp":   float(si.mAmbientTemp),
            "track_temp":     float(si.mTrackTemp),
            "raining":        float(si.mRaining),
            "num_vehicles":   int(si.mNumVehicles),
            "player_name":    _bytes_to_str(si.mPlayerName),
            "game_phase":     int(si.mGamePhase),
            "yellow_state":   int(si.mYellowFlagState),
            "track_length":   float(si.mLapDist),
        }

        # ---------------------------------------------------------
        # Full field — every car in the session, used by the
        # multiclass table and the track map.
        # ---------------------------------------------------------
        field = []
        n = min(si.mNumVehicles, MAX_MAPPED_VEHICLES)
        for i in range(n):
            vs = scoring.mVehicles[i]
            field.append({
                "id":                  int(vs.mID),
                "place":               int(vs.mPlace),
                "driver":              _bytes_to_str(vs.mDriverName),
                "vehicle":             _bytes_to_str(vs.mVehicleName),
                "vehicle_class":       _bytes_to_str(vs.mVehicleClass),
                "total_laps":          int(vs.mTotalLaps),
                "lap_dist":            float(vs.mLapDist),
                "behind_next":         float(vs.mTimeBehindNext),
                "behind_leader":       float(vs.mTimeBehindLeader),
                "laps_behind_leader":  int(vs.mLapsBehindLeader),
                "in_pits":             bool(vs.mInPits),
                "in_garage":           bool(vs.mInGarageStall),
                "is_player":           bool(vs.mIsPlayer),
                "last_lap":            float(vs.mLastLapTime) if vs.mLastLapTime > 0 else None,
                "best_lap":            float(vs.mBestLapTime) if vs.mBestLapTime > 0 else None,
                "pos_x":               float(vs.mPos.x),
                "pos_y":               float(vs.mPos.y),
                "pos_z":               float(vs.mPos.z),
                "sector":              (int(vs.mSector) + 1) if vs.mSector >= 0 else 1,
                "pit_state":           int(vs.mPitState),
            })

        # Compute place_in_class for each car (1-based, by overall place).
        by_class = {}
        for car in field:
            by_class.setdefault(car["vehicle_class"], []).append(car)
        for cls_cars in by_class.values():
            cls_cars.sort(key=lambda c: c["place"] if c["place"] > 0 else 999)
            for idx, c in enumerate(cls_cars, 1):
                c["place_class"] = idx

        out["field"] = field

    return out


# ============================================================
# Demo data (no LMU required)
# ============================================================

def _demo_track_xz(phase: float):
    """Synthetic Le Mans-ish racetrack outline (top-down x/z in meters)."""
    a = phase * 2.0 * math.pi
    x = math.cos(a) * 2200.0 + math.sin(a * 2.0) * 350.0 + math.cos(a * 5.0) * 90.0
    z = math.sin(a) * 1300.0 + math.cos(a * 3.0) * 240.0 - math.sin(a * 7.0) * 60.0
    return x, z


_DEMO_TRACK_LENGTH = 13626.0  # ~Sarthe


def _demo_field(t: float) -> list:
    """15-car synthetic multiclass grid (6 Hypercar / 4 LMP2 / 5 LMGT3)."""
    classes_def = [
        # (name, count, avg lap time)
        ("Hypercar", 6, 210.0),
        ("LMP2",     4, 222.0),
        ("LMGT3",    5, 240.0),
    ]
    player_cls = "Hypercar"
    player_idx_in_class = 2  # 3rd Hypercar

    field = []
    car_id = 1
    for cls_idx, (cls_name, count, cls_lap_t) in enumerate(classes_def):
        for i in range(count):
            # Stagger phases so cars are spread around the track
            base_phase = (cls_idx * 0.11 + i * 0.073)
            phase = (base_phase + t / cls_lap_t) % 1.0
            x, z = _demo_track_xz(phase)
            is_player = (cls_name == player_cls and i == player_idx_in_class)
            total_laps = int((t + base_phase * cls_lap_t) / cls_lap_t)
            field.append({
                "id":               car_id,
                "place":            0,  # filled in below
                "place_class":      0,
                "driver":           "OUR DRIVER" if is_player else f"DRV {car_id:02d}",
                "vehicle":          f"{cls_name} #{car_id:02d}",
                "vehicle_class":    cls_name,
                "total_laps":       total_laps,
                "lap_dist":         phase * _DEMO_TRACK_LENGTH,
                "behind_next":      0.0,
                "behind_leader":    0.0,
                "laps_behind_leader": 0,
                "in_pits":          False,
                "in_garage":        False,
                "is_player":        is_player,
                "last_lap":         cls_lap_t + (i * 0.42),
                "best_lap":         cls_lap_t - 1.8 + (i * 0.25),
                "pos_x":            x,
                "pos_y":            0.0,
                "pos_z":            z,
                "sector":           1 + int(phase * 3),
                "pit_state":        0,
            })
            car_id += 1

    # Overall standings by total progress
    field.sort(
        key=lambda c: c["total_laps"] * _DEMO_TRACK_LENGTH + c["lap_dist"],
        reverse=True,
    )
    leader_dist = field[0]["total_laps"] * _DEMO_TRACK_LENGTH + field[0]["lap_dist"]
    for place, car in enumerate(field, 1):
        car["place"] = place
        car_dist = car["total_laps"] * _DEMO_TRACK_LENGTH + car["lap_dist"]
        speed_for_class = {"Hypercar": 65.0, "LMP2": 60.0, "LMGT3": 50.0}.get(
            car["vehicle_class"], 60.0)
        car["behind_leader"] = (leader_dist - car_dist) / speed_for_class
        if place > 1:
            prev_car = field[place - 2]
            prev_dist = prev_car["total_laps"] * _DEMO_TRACK_LENGTH + prev_car["lap_dist"]
            car["behind_next"] = (prev_dist - car_dist) / speed_for_class

    # Per-class standings
    by_class = {}
    for car in field:
        by_class.setdefault(car["vehicle_class"], []).append(car)
    for cls_cars in by_class.values():
        for idx, c in enumerate(cls_cars, 1):
            c["place_class"] = idx

    return field


def demo_snapshot(t: float) -> dict:
    lap_time = 210.0
    lap_n = int(t // lap_time)
    lap_prog = (t % lap_time) / lap_time

    base = 0.5 + 0.5 * math.sin(t * 2.4)
    rpm = 3200 + base * 7100
    gear = max(1, min(7, 1 + int(base * 6 + 1)))
    speed = 70 + base * 260

    throttle = max(0.0, math.sin(t * 1.1) ** 3)
    brake = max(0.0, -math.sin(t * 1.05 + 0.4)) ** 2
    if throttle > 0.05:
        brake = 0.0

    def tire(base_t, wobble_hz, wear, brake_t):
        return {
            "temp_inner":  base_t + math.sin(t * wobble_hz) * 4 - 2,
            "temp_center": base_t + math.sin(t * (wobble_hz + 0.2)) * 3,
            "temp_outer":  base_t + math.sin(t * (wobble_hz + 0.4)) * 4 + 2,
            "temp_avg":    base_t + math.sin(t * wobble_hz) * 3,
            "pressure":    168 + math.sin(t * 0.5) * 2,
            "wear":        wear,
            "brake_temp":  brake_t + math.sin(t * 0.7) * 30,
            "carcass_temp": base_t - 8,
        }

    cur_s1 = None
    cur_s2 = None
    if lap_prog > 0.33:
        cur_s1 = 52.8 + (math.sin(t * 0.13) * 0.4)
    if lap_prog > 0.66:
        cur_s2 = 74.2 + (math.sin(t * 0.11) * 0.5)

    # Generate full multiclass field once per snapshot
    field = _demo_field(t)
    player_car = next((c for c in field if c["is_player"]), field[0])
    player_x, player_z = player_car["pos_x"], player_car["pos_z"]

    return {
        "status":       "live",
        "track":        "Circuit de la Sarthe",
        "vehicle":      "Porsche 963 #6 LMDh",
        "elapsed_time": t,
        "lap_number":   lap_n,
        "lap_start_et": lap_n * lap_time,

        "speed_kmh":    speed,
        "gear":         gear,
        "rpm":          rpm,
        "max_rpm":      10500.0,

        "fuel":         max(2.0, 102.0 - (t * 0.045) % 100),
        "fuel_capacity": 110.0,
        "water_temp":   89 + math.sin(t * 0.07) * 2.5,
        "oil_temp":     107 + math.sin(t * 0.05) * 3.5,
        "turbo_boost":  1.42,
        "engine_torque": 700.0,

        "throttle":     throttle,
        "brake":        brake,
        "clutch":       0.0,
        "steering":     math.sin(t * 0.6) * 0.35,

        "speed_limiter": lap_prog > 0.97,
        "ignition":     True,
        "headlights":   True,
        "current_sector": 1 + int(lap_prog * 3),

        "tire_compound_front": "MEDIUM",
        "tire_compound_rear":  "MEDIUM",

        "tires": {
            "fl": tire(88, 0.31, 0.94, 420),
            "fr": tire(91, 0.33, 0.94, 415),
            "rl": tire(95, 0.29, 0.92, 380),
            "rr": tire(98, 0.27, 0.91, 385),
        },

        "timing": {
            "place":         player_car["place"],
            "total_laps":    lap_n,
            "current_sector": 1 + int(lap_prog * 3),
            "in_pits":       lap_prog > 0.97,
            "in_garage":     False,
            "under_yellow":  False,
            "pit_state":     0,
            "num_pitstops":  2,
            "num_penalties": 0,
            "flag":          0,
            "time_into_lap": lap_prog * lap_time,
            "estimated_lap": 210.5,
            "last_lap":      210.124,
            "best_lap":      208.912,
            "cur_s1":        cur_s1,
            "cur_s2":        cur_s2,
            "last_s1":       53.123,
            "last_s2":       74.456,
            "last_s3":       82.545,
            "best_s1":       52.812,
            "best_s2":       73.998,
            "behind_next":   player_car["behind_next"],
            "behind_leader": player_car["behind_leader"],
            "laps_behind_leader": 0,
            "driver":        "OUR DRIVER",
            "vehicle_class": "Hypercar",
        },
        "session": {
            "track_name":   "Circuit de la Sarthe",
            "current_et":   t,
            "end_et":       24 * 3600,
            "max_laps":     0,
            "session":      10,
            "ambient_temp": 22.0,
            "track_temp":   28.5,
            "raining":      0.0,
            "num_vehicles": len(field),
            "player_name":  "OUR DRIVER",
            "game_phase":   5,
            "yellow_state": 0,
            "track_length": _DEMO_TRACK_LENGTH,
        },
        "field": field,
        # Tell the client the player's current XZ explicitly so the map
        # doesn't have to find them.
        "player_pos": {"x": player_x, "z": player_z},
    }


# ============================================================
# FastAPI app
# ============================================================

state = {
    "demo": False,
    "hz": DEFAULT_HZ,
    "tel_reader": SharedMemoryReader(TELEMETRY_SHM, rF2Telemetry),
    "sco_reader": SharedMemoryReader(SCORING_SHM, rF2Scoring),
    "boot_t": time.perf_counter(),
    "f1_state": f1_telemetry.F1State(),
    "f1_transport": None,
}


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Start F1 25 UDP listener (best-effort — game may not be running yet)
    try:
        state["f1_transport"] = await f1_telemetry.start_listener(
            state["f1_state"], port=f1_telemetry.DEFAULT_PORT,
        )
        print(f"  [F1]  UDP listener up on :{f1_telemetry.DEFAULT_PORT}", flush=True)
    except OSError as e:
        print(f"  [F1]  could not bind UDP :{f1_telemetry.DEFAULT_PORT}: {e}", flush=True)
    yield
    t = state.get("f1_transport")
    if t is not None:
        try: t.close()
        except Exception: pass


app = FastAPI(title="Pit Wall (LMU + F1)", version="2.0.0", lifespan=lifespan)


# --- snapshot dispatch ---------------------------------------------
def lmu_snapshot(t: float) -> dict:
    if state["demo"]:
        return demo_snapshot(t)
    tel = state["tel_reader"].read()
    sco = state["sco_reader"].read()
    return build_snapshot(tel, sco)


def f1_snapshot(t: float) -> dict:
    if state["demo"]:
        return f1_telemetry.demo_snapshot(t)
    return state["f1_state"].snapshot()


@app.get("/", response_class=HTMLResponse)
async def index():
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML, media_type="text/html")
    return HTMLResponse(
        "<h1 style='font-family:monospace;color:#f55;background:#000;padding:40px'>"
        "static/index.html missing</h1>",
        status_code=500,
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True, "demo": state["demo"], "hz": state["hz"]}


async def _stream(ws: WebSocket, snapshot_fn, label: str):
    await ws.accept()
    try:
        client = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
    except Exception:
        client = "?"
    print(f"  [WS:{label}] connected: {client}", flush=True)
    period = 1.0 / max(1, state["hz"])
    try:
        while True:
            loop_t = time.perf_counter()
            try:
                snap = snapshot_fn(loop_t - state["boot_t"])
            except Exception as exc:
                snap = {"status": "error", "reason": str(exc)}
            try:
                await ws.send_text(json.dumps(snap, separators=(",", ":")))
            except (WebSocketDisconnect, RuntimeError):
                break
            dt = time.perf_counter() - loop_t
            sleep = period - dt
            if sleep > 0:
                await asyncio.sleep(sleep)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"  [WS:{label}] error: {exc}", file=sys.stderr, flush=True)
    finally:
        print(f"  [WS:{label}] disconnected: {client}", flush=True)


# /ws stays as backward-compat alias for the LMU stream
@app.websocket("/ws")
async def ws_default(ws: WebSocket):
    await _stream(ws, lmu_snapshot, "lmu")


@app.websocket("/ws/lmu")
async def ws_lmu(ws: WebSocket):
    await _stream(ws, lmu_snapshot, "lmu")


@app.websocket("/ws/f1")
async def ws_f1(ws: WebSocket):
    await _stream(ws, f1_snapshot, "f1")


# ============================================================
# Entry point
# ============================================================

def _banner(host: str, port: int, demo: bool, hz: int):
    line = "=" * 66
    print()
    print(line)
    print("  LMU PIT WALL  -  Real-time telemetry server  -  v1.0")
    print(line)
    print(f"  Mode          : {'DEMO (synthetic data)' if demo else 'LIVE (rF2 Shared Memory)'}")
    print(f"  Stream rate   : {hz} Hz")
    print(f"  Listening on  : http://{host}:{port}")
    print(f"  Local view    : http://127.0.0.1:{port}/")
    print(f"  WebSocket     : ws://{host}:{port}/ws")
    print()
    print("  Remote access (strategist abroad):")
    print(f"      ngrok http {port}")
    print("      -> copy the https://*.ngrok-free.app URL into the browser")
    print(line)
    print()


def main():
    # Force UTF-8 on stdout/stderr so banner & logs work on cp1251 Windows consoles.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="LMU Pit Wall - real-time WEC-style dashboard "
                    "for Le Mans Ultimate / rFactor 2",
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Listen host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int,
                        help=f"Listen port (default: {DEFAULT_PORT})")
    parser.add_argument("--hz", default=DEFAULT_HZ, type=int,
                        help=f"WebSocket stream rate in Hz (default: {DEFAULT_HZ})")
    parser.add_argument("--demo", action="store_true",
                        help="Stream synthetic data (no LMU required) — useful "
                             "for testing the UI on macOS/Linux or before a race")
    args = parser.parse_args()

    state["demo"] = args.demo
    state["hz"] = max(1, min(120, args.hz))

    if not args.demo and sys.platform != "win32":
        print("  WARN: rF2 Shared Memory is Windows-only. "
              "Use --demo on non-Windows hosts.", flush=True)

    _banner(args.host, args.port, args.demo, state["hz"])

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
