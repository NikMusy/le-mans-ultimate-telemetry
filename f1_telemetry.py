"""
f1_telemetry.py — UDP listener + snapshot builder for the EA / Codemasters
F1 series (F1 22 / 23 / 24 / 25 — same backbone, minor field additions).

The game must have UDP telemetry enabled:
  Settings → Telemetry Settings → UDP Telemetry: On
                                 → UDP Format:    2025 (or Auto)
                                 → UDP Port:      20777

Different packet types arrive at different rates (telemetry/lap/motion are
fast, session/participants/status are slow). We accumulate them in a single
F1State and expose .snapshot() that returns the SAME JSON shape the LMU
backend produces, so the frontend doesn't care which game is upstream.
"""

from __future__ import annotations

import asyncio
import math
import socket
import struct
import time
from typing import Dict, Optional


# ============================================================
# Wire format constants (F1 23/24/25 backbone)
# ============================================================

DEFAULT_PORT = 20777
NUM_CARS = 22  # F1 packets reserve 22 slots

# Packet IDs
PID_MOTION         = 0
PID_SESSION        = 1
PID_LAP_DATA       = 2
PID_EVENT          = 3
PID_PARTICIPANTS   = 4
PID_CAR_SETUPS     = 5
PID_CAR_TELEMETRY  = 6
PID_CAR_STATUS     = 7
PID_FINAL_CLASS    = 8
PID_LOBBY_INFO     = 9
PID_CAR_DAMAGE     = 10
PID_SESSION_HIST   = 11
PID_TYRE_SETS      = 12
PID_MOTION_EX      = 13

# Header is 29 bytes (F1 23+)
HEADER_FMT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
assert HEADER_SIZE == 29, HEADER_SIZE

# CarTelemetryData per car — 60 bytes
CAR_TELEM_FMT = "<HfffBbHBBH4H4B4BH4f4B"
CAR_TELEM_SIZE = struct.calcsize(CAR_TELEM_FMT)
assert CAR_TELEM_SIZE == 60, CAR_TELEM_SIZE

# CarStatusData per car — 55 bytes (F1 23+).
# tractionControl B, antiLockBrakes B, fuelMix B, frontBrakeBias B,
# pitLimiterStatus B, fuelInTank f, fuelCapacity f, fuelRemainingLaps f,
# maxRPM H, idleRPM H, maxGears B, drsAllowed B, drsActivationDistance H,
# actualTyreCompound B, visualTyreCompound B, tyresAgeLaps B, vehicleFiaFlags b,
# enginePowerICE f, enginePowerMGUK f, ersStoreEnergy f, ersDeployMode B,
# ersHarvestedThisLapMGUK f, ersHarvestedThisLapMGUH f, ersDeployedThisLap f,
# networkPaused B
CAR_STATUS_FMT = "<BBBBBfffHHBBHBBBbfffBfffB"
CAR_STATUS_SIZE = struct.calcsize(CAR_STATUS_FMT)
assert CAR_STATUS_SIZE == 55, CAR_STATUS_SIZE

# LapData per car. F1 24 published layout (will work for 25 — tail bytes
# we don't read are harmless even if they grow).
LAP_DATA_FMT = (
    "<II"          # lastLapTimeInMS, currentLapTimeInMS  (4+4)
    "HB"           # sector1TimeMSPart, sector1TimeMinutesPart (2+1)
    "HB"           # sector2TimeMSPart, sector2TimeMinutesPart (2+1)
    "HB"           # deltaToCarInFrontMSPart, deltaToCarInFrontMinutesPart (2+1)
    "HB"           # deltaToRaceLeaderMSPart, deltaToRaceLeaderMinutesPart (2+1)
    "fff"          # lapDistance, totalDistance, safetyCarDelta (4+4+4)
    "BBBBBBBBBBBB" # carPosition, currentLapNum, pitStatus, numPitStops,
                   # sector, currentLapInvalid, penalties, totalWarnings,
                   # cornerCuttingWarnings, numUnservedDriveThroughPens,
                   # numUnservedStopGoPens, gridPosition  (12*1)
    "BB"           # driverStatus, resultStatus (1+1)
    "BHHB"         # pitLaneTimerActive, pitLaneTimeInLaneInMS,
                   # pitStopTimerInMS, pitStopShouldServePen (1+2+2+1)
)
LAP_DATA_SIZE = struct.calcsize(LAP_DATA_FMT)

# CarMotionData per car — 60 bytes (worldPos xyz + velocity xyz + forward dir
# xyz + right dir xyz + g-forces + yaw/pitch/roll). We only need world position.
MOTION_HEAD_FMT = "<fff"   # worldPositionX, Y, Z
MOTION_HEAD_SIZE = struct.calcsize(MOTION_HEAD_FMT)
MOTION_PER_CAR = 60        # full CarMotionData size, F1 23+

# ParticipantsData per car — variable. We just slice out aiControlled (B),
# driverId (B), networkId (B), teamId (B), myTeam (B), raceNumber (B),
# nationality (B), name[32] (32 bytes), yourTelemetry (B), showOnlineNames (B),
# techLevel (H, F1 24+), platform (B)
PARTICIPANT_PREFIX_FMT = "<BBBBBBB32sBBHB"
PARTICIPANT_PREFIX_SIZE = struct.calcsize(PARTICIPANT_PREFIX_FMT)


# ============================================================
# Reference data (track names by trackId, tyre compounds)
# ============================================================

TRACK_NAMES = {
    -1: None,
    0:  "Melbourne",          1:  "Paul Ricard",        2:  "Shanghai",
    3:  "Sakhir",             4:  "Catalunya",          5:  "Monaco",
    6:  "Montreal",           7:  "Silverstone",        8:  "Hockenheim",
    9:  "Hungaroring",        10: "Spa",                11: "Monza",
    12: "Singapore",          13: "Suzuka",             14: "Abu Dhabi",
    15: "Texas",              16: "Brazil",             17: "Austria",
    18: "Sochi",              19: "Mexico",             20: "Baku",
    21: "Sakhir Short",       22: "Silverstone Short",  23: "Texas Short",
    24: "Suzuka Short",       25: "Hanoi",              26: "Zandvoort",
    27: "Imola",              28: "Portimão",           29: "Jeddah",
    30: "Miami",              31: "Las Vegas",          32: "Losail",
    33: "Madrid",             34: "Chicago",            35: "Mexico Reverse",
}

# F1 actual tyre compounds: 16=C5, 17=C4, 18=C3, 19=C2, 20=C1, 21=C0
# 7=inter, 8=wet ;  Visual: 16=soft, 17=med, 18=hard, 7=inter, 8=wet
ACTUAL_TYRE_COMPOUND = {
    16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0",
    7: "INTER", 8: "WET",
    9: "DRY", 10: "WET", 11: "SUPER SOFT", 12: "SOFT", 13: "MEDIUM",
    14: "HARD", 15: "WET",
}
VISUAL_TYRE_COMPOUND = {
    16: "SOFT", 17: "MEDIUM", 18: "HARD", 7: "INTER", 8: "WET",
    19: "SUPER SOFT", 20: "SOFT", 21: "MEDIUM", 22: "HARD",
}


def _decode_track(track_id: int) -> str:
    if track_id is None:
        return "—"
    return TRACK_NAMES.get(track_id, f"Track #{track_id}")


# ============================================================
# State accumulator
# ============================================================

class F1State:
    def __init__(self):
        self.last_packet_at: float = 0.0
        self.player_idx: int = 0
        # Per-car latest values
        self.telem: Dict[int, dict] = {}
        self.lap: Dict[int, dict] = {}
        self.status: Dict[int, dict] = {}
        self.participants: Dict[int, dict] = {}
        self.motion: Dict[int, dict] = {}
        # Session-wide
        self.session_track_id: Optional[int] = None
        self.session: dict = {}
        # Best-lap memory (F1 LapData only exposes last lap; we track best
        # by watching last_lap drops across laps).
        self.best_lap_ms: Dict[int, int] = {}

    # --------------------------------------------------------
    # Packet entry point
    # --------------------------------------------------------
    def handle(self, data: bytes):
        if len(data) < HEADER_SIZE:
            return
        try:
            (packet_format, game_year, game_major, game_minor, packet_version,
             packet_id, session_uid, session_time, frame_id, overall_frame,
             player_idx, secondary_idx) = struct.unpack_from(HEADER_FMT, data, 0)
        except struct.error:
            return
        self.last_packet_at = time.time()
        self.player_idx = int(player_idx) if 0 <= player_idx < NUM_CARS else 0

        if packet_id == PID_CAR_TELEMETRY:
            self._parse_telemetry(data)
        elif packet_id == PID_LAP_DATA:
            self._parse_lap_data(data)
        elif packet_id == PID_SESSION:
            self._parse_session(data)
        elif packet_id == PID_CAR_STATUS:
            self._parse_car_status(data)
        elif packet_id == PID_PARTICIPANTS:
            self._parse_participants(data)
        elif packet_id == PID_MOTION:
            self._parse_motion(data)

    # --------------------------------------------------------
    # Parsers
    # --------------------------------------------------------
    def _parse_telemetry(self, data: bytes):
        off = HEADER_SIZE
        for car_idx in range(NUM_CARS):
            if off + CAR_TELEM_SIZE > len(data):
                break
            (speed, throttle, steer, brake, clutch, gear, engine_rpm, drs,
             rl_pct, rl_bits,
             bt0, bt1, bt2, bt3,
             tsurf0, tsurf1, tsurf2, tsurf3,
             tinn0, tinn1, tinn2, tinn3,
             engine_temp,
             tp0, tp1, tp2, tp3,
             st0, st1, st2, st3) = struct.unpack_from(CAR_TELEM_FMT, data, off)
            self.telem[car_idx] = {
                "speed": speed,                # km/h
                "throttle": throttle,
                "steer": steer,
                "brake": brake,
                "clutch": clutch / 100.0,
                "gear": gear,
                "rpm": engine_rpm,
                "drs": bool(drs),
                "rev_lights_pct": rl_pct,
                # F1 tyre index order: 0=RL, 1=RR, 2=FL, 3=FR
                "brake_temp_RL": bt0, "brake_temp_RR": bt1,
                "brake_temp_FL": bt2, "brake_temp_FR": bt3,
                "tyre_surface_RL": tsurf0, "tyre_surface_RR": tsurf1,
                "tyre_surface_FL": tsurf2, "tyre_surface_FR": tsurf3,
                "tyre_inner_RL": tinn0, "tyre_inner_RR": tinn1,
                "tyre_inner_FL": tinn2, "tyre_inner_FR": tinn3,
                "engine_temp": engine_temp,
                "tyre_pressure_RL": tp0, "tyre_pressure_RR": tp1,
                "tyre_pressure_FL": tp2, "tyre_pressure_FR": tp3,
            }
            off += CAR_TELEM_SIZE

    def _parse_lap_data(self, data: bytes):
        off = HEADER_SIZE
        for car_idx in range(NUM_CARS):
            if off + LAP_DATA_SIZE > len(data):
                break
            try:
                vals = struct.unpack_from(LAP_DATA_FMT, data, off)
            except struct.error:
                break
            (last_lap_ms, cur_lap_ms,
             s1_ms, s1_min, s2_ms, s2_min,
             dnext_ms, dnext_min, dlead_ms, dlead_min,
             lap_dist, total_dist, sc_delta,
             pos, cur_lap_num, pit_status, num_pits,
             sector, cur_lap_invalid, penalties, total_warnings,
             corner_warnings, dt_pens, sg_pens, grid_pos,
             driver_status, result_status,
             pit_lane_active, pit_lane_ms, pit_stop_ms, pit_serve) = vals

            last_lap_full = last_lap_ms  # already in ms
            cur_lap_full = cur_lap_ms
            s1_full = s1_min * 60000 + s1_ms
            s2_full = s2_min * 60000 + s2_ms
            gap_next = (dnext_min * 60000 + dnext_ms)
            gap_lead = (dlead_min * 60000 + dlead_ms)

            self.lap[car_idx] = {
                "last_lap_ms": last_lap_full,
                "cur_lap_ms":  cur_lap_full,
                "sector1_ms":  s1_full,
                "sector2_ms":  s2_full,
                "delta_next_ms": gap_next,
                "delta_leader_ms": gap_lead,
                "lap_dist": lap_dist,
                "total_dist": total_dist,
                "position": pos,
                "lap_num": cur_lap_num,
                "pit_status": pit_status,
                "num_pits": num_pits,
                "sector": sector,
                "lap_invalid": bool(cur_lap_invalid),
                "penalties_sec": penalties,
                "driver_status": driver_status,
                "result_status": result_status,
            }
            # Track best lap
            if last_lap_full and last_lap_full > 0:
                prev = self.best_lap_ms.get(car_idx)
                if prev is None or last_lap_full < prev:
                    self.best_lap_ms[car_idx] = last_lap_full

            off += LAP_DATA_SIZE

    def _parse_session(self, data: bytes):
        # Read the leading fixed fields we actually use; everything after is
        # forecast samples / marshal zones we don't need.
        try:
            (weather, track_temp, air_temp, total_laps, track_length,
             session_type, track_id, formula, session_time_left,
             session_duration, pit_speed_limit,
             game_paused, is_spectating, spectator_idx, sli_pro,
             num_marshal_zones) = struct.unpack_from(
                "<BbbBHBbBHHBBBBBB", data, HEADER_SIZE
            )
        except struct.error:
            return
        self.session_track_id = track_id
        self.session = {
            "weather": weather,
            "track_temp": track_temp,
            "air_temp": air_temp,
            "total_laps": total_laps,
            "track_length": track_length,
            "session_type": session_type,
            "track_id": track_id,
            "formula": formula,
            "session_time_left": session_time_left,
            "session_duration": session_duration,
            "pit_speed_limit": pit_speed_limit,
            "is_paused": bool(game_paused),
        }

    def _parse_car_status(self, data: bytes):
        off = HEADER_SIZE
        for car_idx in range(NUM_CARS):
            if off + CAR_STATUS_SIZE > len(data):
                break
            try:
                vals = struct.unpack_from(CAR_STATUS_FMT, data, off)
            except struct.error:
                break
            (tc, abs_, fuel_mix, front_bias, pit_lim,
             fuel, fuel_cap, fuel_laps,
             max_rpm, idle_rpm, max_gears, drs_allowed, drs_act_dist,
             actual_tc, visual_tc, tyre_age_laps, fia_flags,
             eng_ice, eng_mguk, ers_store, ers_deploy_mode,
             ers_harv_mguk, ers_harv_mguh, ers_deployed, net_paused) = vals
            self.status[car_idx] = {
                "fuel_mix": fuel_mix,
                "front_brake_bias": front_bias,
                "pit_limiter": bool(pit_lim),
                "fuel": fuel,
                "fuel_capacity": fuel_cap,
                "fuel_remaining_laps": fuel_laps,
                "max_rpm": max_rpm,
                "drs_allowed": bool(drs_allowed),
                "actual_tyre_compound": actual_tc,
                "visual_tyre_compound": visual_tc,
                "tyre_age_laps": tyre_age_laps,
                "fia_flags": fia_flags,
                "ers_store": ers_store,
                "ers_deploy_mode": ers_deploy_mode,
                "ers_deployed_this_lap": ers_deployed,
            }
            off += CAR_STATUS_SIZE

    def _parse_participants(self, data: bytes):
        try:
            (num_active,) = struct.unpack_from("<B", data, HEADER_SIZE)
        except struct.error:
            return
        off = HEADER_SIZE + 1
        for car_idx in range(NUM_CARS):
            if off + PARTICIPANT_PREFIX_SIZE > len(data):
                break
            try:
                (ai_ctrl, drv_id, net_id, team_id, my_team, race_num,
                 nationality, name_bytes, your_telem, show_online,
                 tech_level, platform) = struct.unpack_from(
                    PARTICIPANT_PREFIX_FMT, data, off
                )
            except struct.error:
                break
            try:
                name = name_bytes.split(b"\x00", 1)[0].decode("utf-8", "replace")
            except Exception:
                name = "—"
            self.participants[car_idx] = {
                "ai": bool(ai_ctrl),
                "team_id": team_id,
                "race_number": race_num,
                "driver_name": name,
            }
            off += PARTICIPANT_PREFIX_SIZE

    def _parse_motion(self, data: bytes):
        off = HEADER_SIZE
        for car_idx in range(NUM_CARS):
            if off + MOTION_PER_CAR > len(data):
                break
            try:
                (x, y, z) = struct.unpack_from(MOTION_HEAD_FMT, data, off)
            except struct.error:
                break
            self.motion[car_idx] = {"x": x, "y": y, "z": z}
            off += MOTION_PER_CAR

    # --------------------------------------------------------
    # Snapshot in the LMU-compatible JSON shape
    # --------------------------------------------------------
    def snapshot(self) -> dict:
        if not self.telem and not self.lap:
            return {"status": "waiting", "reason": "no F1 packets yet (enable UDP telemetry in game settings)"}
        # Has the player car appeared?
        idx = self.player_idx
        t = self.telem.get(idx)
        if t is None:
            return {"status": "waiting", "reason": "waiting for player car telemetry"}

        l = self.lap.get(idx, {})
        s = self.status.get(idx, {})
        p = self.participants.get(idx, {})
        m = self.motion.get(idx, {})

        def f_tyre(pos):
            return {
                "temp_inner":  t.get(f"tyre_inner_{pos}"),
                "temp_center": t.get(f"tyre_inner_{pos}"),  # F1 has no center channel
                "temp_outer":  t.get(f"tyre_surface_{pos}"),
                "temp_avg":    (t.get(f"tyre_inner_{pos}", 0) +
                                t.get(f"tyre_surface_{pos}", 0)) / 2.0,
                # F1 reports PSI; the LMU pipeline expects kPa. PSI->kPa = *6.895
                "pressure":    t.get(f"tyre_pressure_{pos}", 0) * 6.895,
                "wear":        1.0,  # comes via Car Damage; left high for now
                "brake_temp":  t.get(f"brake_temp_{pos}"),
                "carcass_temp": t.get(f"tyre_inner_{pos}"),
            }

        speed_kmh = t.get("speed", 0) or 0
        gear = t.get("gear", 0)
        rpm = t.get("rpm", 0)
        max_rpm = s.get("max_rpm") or 15000

        track_name = _decode_track(self.session_track_id)
        track_length = (self.session or {}).get("track_length") or 0

        out = {
            "status":        "live",
            "track":         track_name,
            "vehicle":       f"{p.get('driver_name','—')} · #{p.get('race_number','?')}",
            "elapsed_time":  0.0,
            "lap_number":    l.get("lap_num", 0),
            "lap_start_et":  0.0,

            "speed_kmh":     float(speed_kmh),
            "gear":          int(gear),
            "rpm":           float(rpm),
            "max_rpm":       float(max_rpm),

            "fuel":          s.get("fuel", 0.0),
            "fuel_capacity": s.get("fuel_capacity"),
            "water_temp":    t.get("engine_temp", 0),   # engine temperature
            "oil_temp":      t.get("engine_temp", 0),   # F1 doesn't separate
            "turbo_boost":   0.0,
            "engine_torque": 0.0,

            "throttle":      float(t.get("throttle", 0)),
            "brake":         float(t.get("brake", 0)),
            "clutch":        float(t.get("clutch", 0)),
            "steering":      float(t.get("steer", 0)),

            "speed_limiter": bool(s.get("pit_limiter")),
            "ignition":      True,
            "headlights":    False,
            "current_sector": (l.get("sector", 0) + 1) if l else 1,

            "tire_compound_front": ACTUAL_TYRE_COMPOUND.get(s.get("actual_tyre_compound"), "—"),
            "tire_compound_rear":  ACTUAL_TYRE_COMPOUND.get(s.get("actual_tyre_compound"), "—"),

            "tires": {
                "fl": f_tyre("FL"),
                "fr": f_tyre("FR"),
                "rl": f_tyre("RL"),
                "rr": f_tyre("RR"),
            },
            "drs":           bool(t.get("drs")),
            "ers_store":     s.get("ers_store"),
            "fuel_laps_left": s.get("fuel_remaining_laps"),
        }

        # Timing
        last_lap_s = (l.get("last_lap_ms") or 0) / 1000.0 or None
        cur_lap_s = (l.get("cur_lap_ms") or 0) / 1000.0
        best_ms = self.best_lap_ms.get(idx)
        best_lap_s = (best_ms / 1000.0) if best_ms else None
        s1_s = (l.get("sector1_ms") or 0) / 1000.0 or None
        s2_s = (l.get("sector2_ms") or 0) / 1000.0 or None

        out["timing"] = {
            "place":         l.get("position", 0),
            "total_laps":    l.get("lap_num", 0),
            "current_sector": (l.get("sector", 0) + 1) if l else 1,
            "in_pits":       l.get("pit_status", 0) > 0,
            "in_garage":     False,
            "under_yellow":  False,
            "pit_state":     l.get("pit_status", 0),
            "num_pitstops":  l.get("num_pits", 0),
            "num_penalties": l.get("penalties_sec", 0),
            "flag":          0,
            "time_into_lap": cur_lap_s if cur_lap_s > 0 else None,
            "estimated_lap": None,
            "last_lap":      last_lap_s,
            "best_lap":      best_lap_s,
            "cur_s1":        s1_s,
            "cur_s2":        s2_s,
            "last_s1":       s1_s,
            "last_s2":       s2_s,
            "last_s3":       None,
            "best_s1":       None,
            "best_s2":       None,
            "behind_next":   (l.get("delta_next_ms") or 0) / 1000.0,
            "behind_leader": (l.get("delta_leader_ms") or 0) / 1000.0,
            "laps_behind_leader": 0,
            "driver":        p.get("driver_name", "—"),
            "vehicle_class": "F1",
        }
        out["session"] = {
            "track_name":   track_name,
            "current_et":   0.0,
            "end_et":       0.0,
            "max_laps":     (self.session or {}).get("total_laps", 0),
            "session":      (self.session or {}).get("session_type", 0),
            "ambient_temp": (self.session or {}).get("air_temp", 0),
            "track_temp":   (self.session or {}).get("track_temp", 0),
            "raining":      0.0,
            "num_vehicles": len(self.telem),
            "player_name":  p.get("driver_name", "—"),
            "game_phase":   0,
            "yellow_state": 0,
            "track_length": float(track_length),
        }

        # Full field
        field = []
        for i in range(NUM_CARS):
            li = self.lap.get(i)
            pi = self.participants.get(i)
            mi = self.motion.get(i, {})
            if li is None and pi is None:
                continue
            if li and li.get("result_status", 0) in (0, 7):
                # 0=invalid, 7=inactive
                if not pi:
                    continue
            cls = "F1"
            field.append({
                "id":               i,
                "place":            (li or {}).get("position", 0),
                "place_class":      (li or {}).get("position", 0),
                "driver":           (pi or {}).get("driver_name", f"CAR {i}"),
                "vehicle":          f"#{(pi or {}).get('race_number','?')}",
                "vehicle_class":    cls,
                "total_laps":       (li or {}).get("lap_num", 0),
                "lap_dist":         (li or {}).get("lap_dist", 0.0),
                "behind_next":      ((li or {}).get("delta_next_ms") or 0) / 1000.0,
                "behind_leader":    ((li or {}).get("delta_leader_ms") or 0) / 1000.0,
                "laps_behind_leader": 0,
                "in_pits":          (li or {}).get("pit_status", 0) > 0,
                "in_garage":        False,
                "is_player":        (i == idx),
                "last_lap":         ((li or {}).get("last_lap_ms") or 0) / 1000.0 or None,
                "best_lap":         (self.best_lap_ms.get(i) or 0) / 1000.0 or None,
                "pos_x":            mi.get("x", 0.0),
                "pos_y":            mi.get("y", 0.0),
                "pos_z":            mi.get("z", 0.0),
                "sector":           ((li or {}).get("sector", 0) + 1),
                "pit_state":        (li or {}).get("pit_status", 0),
            })
        out["field"] = field

        pm = self.motion.get(idx, {})
        out["player_pos"] = {"x": pm.get("x", 0.0), "z": pm.get("z", 0.0)}
        return out


# ============================================================
# Async UDP listener
# ============================================================

class _F1Protocol(asyncio.DatagramProtocol):
    def __init__(self, state: F1State):
        self.state = state

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            self.state.handle(data)
        except Exception:
            pass


async def start_listener(state: F1State,
                         host: str = "0.0.0.0",
                         port: int = DEFAULT_PORT) -> asyncio.DatagramTransport:
    loop = asyncio.get_running_loop()
    transport, _proto = await loop.create_datagram_endpoint(
        lambda: _F1Protocol(state),
        local_addr=(host, port),
        reuse_port=False,
        family=socket.AF_INET,
    )
    return transport


# ============================================================
# Demo data generator (no F1 game required)
# ============================================================

def demo_snapshot(t: float) -> dict:
    """Synthetic F1 snapshot mirroring the LMU demo cadence."""
    lap_time = 90.0   # ~90s F1 lap on a short track
    lap_n = int(t // lap_time)
    lap_prog = (t % lap_time) / lap_time
    base = 0.5 + 0.5 * math.sin(t * 2.0)
    rpm = 6000 + base * 9500
    speed = 60 + base * 270
    gear = max(1, min(8, int(1 + base * 7)))

    throttle = max(0.0, math.sin(t * 1.2) ** 3)
    brake = max(0.0, -math.sin(t * 1.05 + 0.4)) ** 2
    if throttle > 0.05:
        brake = 0.0

    def tyre(base_t, brk_base):
        return {
            "temp_inner":  base_t + math.sin(t * 0.3) * 3,
            "temp_center": base_t + math.sin(t * 0.3) * 3,
            "temp_outer":  base_t - 4 + math.sin(t * 0.3) * 3,
            "temp_avg":    base_t,
            "pressure":    151.7,  # ~22psi -> kPa
            "wear":        max(0.4, 1.0 - (t * 0.0008) % 0.6),
            "brake_temp":  brk_base + math.sin(t * 0.7) * 25,
            "carcass_temp": base_t - 6,
        }

    field = []
    for i in range(20):
        phase = (i * 0.05 + t / lap_time) % 1.0
        ang = phase * 2 * math.pi
        x = math.cos(ang) * 1400 + math.sin(ang * 2) * 200
        z = math.sin(ang) * 900 + math.cos(ang * 3) * 150
        is_player = (i == 2)
        field.append({
            "id": i, "place": i + 1, "place_class": i + 1,
            "driver": "DRIVER" if is_player else f"AI {i:02d}",
            "vehicle": f"#{44 if is_player else 10+i}",
            "vehicle_class": "F1",
            "total_laps": lap_n,
            "lap_dist": phase * 5800,
            "behind_next": 0.45,
            "behind_leader": i * 1.2,
            "laps_behind_leader": 0,
            "in_pits": False, "in_garage": False, "is_player": is_player,
            "last_lap": 90.5 + i * 0.15, "best_lap": 89.8 + i * 0.1,
            "pos_x": x, "pos_y": 0.0, "pos_z": z,
            "sector": 1 + int(phase * 3), "pit_state": 0,
        })
    player = field[2]

    return {
        "status":       "live",
        "track":        "Silverstone",
        "vehicle":      "DRIVER · #44",
        "elapsed_time": t,
        "lap_number":   lap_n,
        "lap_start_et": lap_n * lap_time,
        "speed_kmh":    speed,
        "gear":         gear,
        "rpm":          rpm,
        "max_rpm":      15000.0,
        "fuel":         max(2.0, 102.0 - (t * 0.07) % 100),
        "fuel_capacity": 110.0,
        "water_temp":   95 + math.sin(t * 0.1) * 3,
        "oil_temp":     108 + math.sin(t * 0.07) * 4,
        "turbo_boost":  0.0,
        "engine_torque": 0.0,
        "throttle":     throttle,
        "brake":        brake,
        "clutch":       0.0,
        "steering":     math.sin(t * 0.6) * 0.4,
        "speed_limiter": lap_prog > 0.97,
        "ignition":     True,
        "headlights":   False,
        "current_sector": 1 + int(lap_prog * 3),
        "tire_compound_front": "MEDIUM",
        "tire_compound_rear":  "MEDIUM",
        "tires": {
            "fl": tyre(95, 480), "fr": tyre(96, 475),
            "rl": tyre(102, 420), "rr": tyre(101, 425),
        },
        "drs":          False,
        "ers_store":    2000000.0,
        "fuel_laps_left": 8.0,
        "timing": {
            "place":         player["place"],
            "total_laps":    lap_n,
            "current_sector": 1 + int(lap_prog * 3),
            "in_pits":       lap_prog > 0.97,
            "in_garage":     False,
            "under_yellow":  False,
            "pit_state":     0,
            "num_pitstops":  1,
            "num_penalties": 0,
            "flag":          0,
            "time_into_lap": lap_prog * lap_time,
            "estimated_lap": 90.0,
            "last_lap":      90.124,
            "best_lap":      89.712,
            "cur_s1":        24.3,
            "cur_s2":        32.1,
            "last_s1":       24.5,
            "last_s2":       32.4,
            "last_s3":       33.2,
            "best_s1":       24.2,
            "best_s2":       31.9,
            "behind_next":   0.45,
            "behind_leader": 2.4,
            "laps_behind_leader": 0,
            "driver":        "DRIVER",
            "vehicle_class": "F1",
        },
        "session": {
            "track_name":   "Silverstone",
            "current_et":   t,
            "end_et":       3600,
            "max_laps":     0,
            "session":      10,
            "ambient_temp": 22.0,
            "track_temp":   34.0,
            "raining":      0.0,
            "num_vehicles": 20,
            "player_name":  "DRIVER",
            "game_phase":   5,
            "yellow_state": 0,
            "track_length": 5891.0,
        },
        "field": field,
        "player_pos": {"x": player["pos_x"], "z": player["pos_z"]},
    }
