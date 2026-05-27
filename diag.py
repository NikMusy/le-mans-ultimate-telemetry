r"""
LMU PIT WALL — Shared Memory diagnostics
=========================================

Read-only probe for the rF2 Shared Memory Map Plugin. Run this BEFORE
trusting the dashboard in a race:

    .\.venv\Scripts\python.exe diag.py

What it does
------------
* Opens $rFactor2SMMP_Telemetry$ and $rFactor2SMMP_Scoring$.
* Prints raw values of every field the dashboard reads.
* Flags anything that looks bogus (NaN, negative fuel, > 1e6 RPM, tires
  hotter than the surface of Mercury, etc.) — that means struct layout
  drifted between this code and your SMMP plugin DLL version.

Run it while LMU is on track (Practice or Race). If you see sane values
in this output, the dashboard will see the same. If you see garbage,
ping me with the output and we'll patch the struct.
"""

from __future__ import annotations

import sys
import time

# Reuse the structures already defined in server.py
sys.path.insert(0, '.')
from server import (
    rF2Telemetry, rF2Scoring,
    SharedMemoryReader,
    TELEMETRY_SHM, SCORING_SHM,
    KELVIN,
    _bytes_to_str, _find_player,
)
import math
import ctypes


# ============================================================
# Sanity checkers
# ============================================================
def check(label: str, value, ok_min=None, ok_max=None, unit: str = "") -> str:
    """Return a coloured-text line: OK / WARN / FAIL."""
    bad = False
    note = ""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            bad, note = True, "NaN/Inf"
    if not bad and ok_min is not None and value < ok_min:
        bad, note = True, f"< {ok_min}"
    if not bad and ok_max is not None and value > ok_max:
        bad, note = True, f"> {ok_max}"
    tag = "[FAIL]" if bad else "[ OK ]"
    val_str = f"{value:>14.3f}" if isinstance(value, float) else f"{value!r:>14}"
    note_str = f"  ({note})" if note else ""
    return f"  {tag} {label:30s} = {val_str} {unit}{note_str}"


def hr(title: str = ""):
    if title:
        print(f"\n--- {title} {'-' * (60 - len(title))}")
    else:
        print("-" * 64)


# ============================================================
# Probe
# ============================================================
def main():
    print("=" * 64)
    print("  LMU PIT WALL  -  Shared Memory diagnostics")
    print("=" * 64)
    print(f"  Telemetry section size: {ctypes.sizeof(rF2Telemetry):>10} bytes")
    print(f"  Scoring   section size: {ctypes.sizeof(rF2Scoring):>10} bytes")
    print()
    print("  Connecting to shared memory sections...")

    tel_r = SharedMemoryReader(TELEMETRY_SHM, rF2Telemetry)
    sco_r = SharedMemoryReader(SCORING_SHM, rF2Scoring)

    tel = tel_r.read()
    sco = sco_r.read()

    if tel is None:
        print("  [FAIL] Could NOT open Telemetry SHM.")
        print("         Is LMU running? Is the SMMP plugin loaded?")
        sys.exit(1)
    if sco is None:
        print("  [WARN] Could NOT open Scoring SHM. Timing tower will be empty.")

    print(f"  [ OK ] Telemetry SHM opened. mNumVehicles = {tel.mNumVehicles}")
    if sco is not None:
        print(f"  [ OK ] Scoring   SHM opened. mNumVehicles = {sco.mScoringInfo.mNumVehicles}")

    if tel.mNumVehicles <= 0:
        print("\n  No active vehicles — you are probably in the menu or garage.")
        print("  Go on track in LMU (Practice / Race) and rerun.")
        sys.exit(0)

    # Find the player
    idx, ps = _find_player(sco, tel)
    v = tel.mVehicles[idx]
    print(f"\n  Player car: telemetry index = {idx}, mID = {v.mID}")
    print(f"  Vehicle name : {_bytes_to_str(v.mVehicleName)!r}")
    print(f"  Track name   : {_bytes_to_str(v.mTrackName)!r}")
    if ps is not None:
        print(f"  Driver name  : {_bytes_to_str(ps.mDriverName)!r}")
        print(f"  Vehicle class: {_bytes_to_str(ps.mVehicleClass)!r}")
        print(f"  Is player    : {bool(ps.mIsPlayer)}")
        print(f"  Place        : P{ps.mPlace}")

    # ============================================================
    # Telemetry sanity
    # ============================================================
    hr("ENGINE")
    speed_ms = math.sqrt(v.mLocalVel.x**2 + v.mLocalVel.y**2 + v.mLocalVel.z**2)
    print(check("speed (km/h)",          speed_ms * 3.6,        0,    500, "km/h"))
    print(check("gear",                  v.mGear,              -1,     10))
    print(check("RPM",                   v.mEngineRPM,          0, 30000, "rpm"))
    print(check("max RPM",               v.mEngineMaxRPM,    1000, 25000, "rpm"))
    print(check("water temp",            v.mEngineWaterTemp,   30,    180, "C"))
    print(check("oil temp",              v.mEngineOilTemp,     30,    200, "C"))
    print(check("turbo boost",           v.mTurboBoostPressure, 0,     10, "bar"))

    hr("FUEL")
    print(check("fuel",                  v.mFuel,               0,    400, "L"))
    print(check("fuel capacity",         v.mFuelCapacity,       0,    400, "L"))

    hr("INPUTS")
    print(check("throttle (unfiltered)", v.mUnfilteredThrottle, 0,      1))
    print(check("brake    (unfiltered)", v.mUnfilteredBrake,    0,      1))
    print(check("clutch   (unfiltered)", v.mUnfilteredClutch,   0,      1))
    print(check("steering (unfiltered)", v.mUnfilteredSteering, -1.5, 1.5))

    hr("WHEELS  (FL FR RL RR)")
    wheel_names = ["FL", "FR", "RL", "RR"]
    for i, w in enumerate(v.mWheels):
        temps_c = [t - KELVIN for t in w.mTemperature]
        print(f"  {wheel_names[i]}:")
        print(check("  temp inner  (C)",  temps_c[0],          -30, 250, "C"))
        print(check("  temp center (C)",  temps_c[1],          -30, 250, "C"))
        print(check("  temp outer  (C)",  temps_c[2],          -30, 250, "C"))
        print(check("  carcass temp (C)", w.mTireCarcassTemperature - KELVIN, -30, 250, "C"))
        print(check("  brake temp  (C)",  w.mBrakeTemp - KELVIN,             -30, 1500, "C"))
        print(check("  pressure (kPa)",   w.mPressure,           0, 500, "kPa"))
        print(check("  wear (0..1)",      w.mWear,               0,   1))

    # ============================================================
    # Scoring sanity
    # ============================================================
    if ps is not None:
        hr("TIMING (Scoring)")
        si = sco.mScoringInfo
        print(check("session number",        si.mSession,           0,   20))
        print(check("game phase",            si.mGamePhase,         0,   20))
        print(check("track length (m)",      si.mLapDist,         500, 30000, "m"))
        print(check("ambient temp (C)",      si.mAmbientTemp,     -30,    60, "C"))
        print(check("track temp (C)",        si.mTrackTemp,       -30,    80, "C"))
        print(check("num vehicles",          si.mNumVehicles,       0,   128))
        print(check("player place",          ps.mPlace,             1,   128))
        print(check("total laps",            ps.mTotalLaps,         0,   500))
        print(check("current sector",        int(ps.mSector) + 1,   0,     4))
        print(check("last lap (s)",          ps.mLastLapTime,      -1,  3600))
        print(check("best lap (s)",          ps.mBestLapTime,      -1,  3600))
        print(check("time into lap",         ps.mTimeIntoLap,      -1,  3600))
        print(check("in pits",               int(ps.mInPits),       0,     1))

        hr(f"FIELD ({si.mNumVehicles} cars)")
        for i in range(min(si.mNumVehicles, 30)):
            vs = sco.mVehicles[i]
            tag = "*ME*" if vs.mIsPlayer else "    "
            print(f"  {tag} P{vs.mPlace:>3}  "
                  f"{_bytes_to_str(vs.mDriverName)[:18]:<18}  "
                  f"cls={_bytes_to_str(vs.mVehicleClass)[:10]:<10}  "
                  f"lap={vs.mTotalLaps:>3}  "
                  f"dist={vs.mLapDist:>8.0f} m  "
                  f"pos=({vs.mPos.x:>7.0f}, {vs.mPos.z:>7.0f})  "
                  f"{'PIT' if vs.mInPits else '   '}")

    print()
    print("=" * 64)
    print("  Done. If anything is [FAIL] or visibly wrong (e.g. fuel=-12345,")
    print("  tires at 500 C), the SMMP struct layout drifted - send this")
    print("  output to fix.")
    print("=" * 64)


if __name__ == "__main__":
    main()
