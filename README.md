# LMU PIT WALL

> WEC-style real-time telemetry dashboard for **Le Mans Ultimate** (rFactor 2 engine).
> Built for endurance racing — your remote strategist watches the same data as a real pit-wall engineer.

![tech](https://img.shields.io/badge/stack-Python%20·%20FastAPI%20·%20WebSocket-black?style=flat-square)
![engine](https://img.shields.io/badge/engine-rFactor%202-red?style=flat-square)
![platform](https://img.shields.io/badge/platform-Windows-blue?style=flat-square)
![license](https://img.shields.io/badge/license-MIT-green?style=flat-square)

A self-hosted alternative to services like *mylmu*. Reads the rF2 Shared Memory
Map Plugin sections directly, streams a compact JSON snapshot over WebSocket at
50 Hz, and renders it as a hardcore monospace dashboard that mirrors the look
of a real WEC pit wall.

## Features

- **Live telemetry @ 50 Hz** — gear, RPM, speed, throttle/brake/clutch, steering
- **4-wheel tire panel** — inner/center/outer temperatures with cold→optimal→hot colour mapping, pressure (PSI + kPa), wear, carcass and brake temps
- **Car status** — fuel (with low/critical warning), water temp, oil temp, turbo boost
- **Timing tower** — position, lap, current/last/best times, full S1/S2/S3 table with delta column, session-best (magenta) highlighting, active sector indicator
- **Strategy assist** — rolling fuel-per-lap, laps-left estimate, stop count, penalties, gap to ahead/leader
- **Flag strip** — LIVE / PIT / SPDLIM / OVERHEAT / FUEL! / YELLOW
- **Auto-reconnect** WebSocket with exponential backoff
- **`--demo` mode** — runs without LMU, perfect for testing the UI on macOS/Linux
- **Single-file frontend** — no build step, no npm, no framework

## Architecture

```
┌──────────────┐    Shared Memory    ┌──────────────┐   WebSocket 50Hz   ┌──────────────┐
│ Le Mans      │ ──────────────────► │  server.py   │ ─────────────────► │  index.html  │
│ Ultimate +   │   $rFactor2SMMP_    │  FastAPI +   │      ws://         │  Vanilla JS  │
│ rF2 SMMP     │     Telemetry$      │  ctypes      │     /ws            │  Monospace   │
│ Plugin       │     Scoring$        │  parser      │                    │  Dashboard   │
└──────────────┘                     └──────────────┘                    └──────────────┘
                                                            ngrok / cloudflared
                                                                   │
                                                                   ▼
                                                       Strategist's browser anywhere
```

## Prerequisites

1. **Le Mans Ultimate** installed (Windows).
2. **rF2 Shared Memory Map Plugin (TheIronWolfMod)** — required so that the
   `$rFactor2SMMP_Telemetry$` / `$rFactor2SMMP_Scoring$` sections exist:
   - download `rF2SharedMemoryMapPlugin64.dll` from the original repo
   - drop it into `<LMU install>\Bin64\Plugins\`
   - enable the plugin inside the game (`Settings → Plugins`)
3. **Python 3.10+**.

## Install

```powershell
git clone https://github.com/<your-user>/le-mans-ultimate-telemetry.git
cd le-mans-ultimate-telemetry

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install fastapi "uvicorn[standard]"
```

## Run

```powershell
# Demo mode — synthetic data, no LMU required (great for UI checks)
python server.py --demo

# Production — LMU is running, plugin is loaded
python server.py

# Custom port / rate
python server.py --host 0.0.0.0 --port 8000 --hz 60
```

Open `http://127.0.0.1:8000/` — you should see the full dashboard. If LMU is in
the main menu the UI will display **AWAITING TELEMETRY**; the server itself
will not crash.

## Remote access for your strategist

In a second terminal, while `server.py` is running:

```powershell
ngrok http 8000
```

Copy the `https://*.ngrok-free.app` URL from ngrok's output and send it to
your strategist. WebSockets use `wss://` automatically — the frontend handles
this without changes.

For 24h races, reserve a fixed ngrok subdomain:

```powershell
ngrok http --domain=your-reserved-name.ngrok-free.app 8000
```

## Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ● LMU PIT WALL  · CKT · CAR · DRV · CLS · AIR/TRK/WET · sessionclock         │
├────────────────────────────┬──────────────────┬──────────────────────────────┤
│           VITALS            │      TIRES       │      TIMING TOWER           │
│                             │  ┌────┐  ┌────┐  │  ┌──────────┐ ┌──────────┐  │
│   ┌───────────────────┐    │  │ FL │  │ FR │  │  │ POSITION │ │   LAP    │  │
│   │                   │    │  └────┘  └────┘  │  │    03    │ │    47    │  │
│   │        6          │    │  ┌────┐  ┌────┐  │  ├──────────┴─┴──────────┤  │
│   │                   │    │  │ RL │  │ RR │  │  │ CURR  3:31.423        │  │
│   └───────────────────┘    │  └────┘  └────┘  │  │ LAST  3:30.124        │  │
│   [████ shift lights]      │                  │  │ BEST  3:28.912        │  │
│                             ├──────────────────┤  ├──────────────────────────┤
│   THR  BRK  CLT             │   CAR STATUS     │  │ S1 / S2 / S3 / Δ       │  │
│                             │  FUEL 84.3L      │  │ ...                    │  │
│   [── STEERING ──]          │  WATER 88° OIL 96│  │ GAPS · FLAGS           │  │
└────────────────────────────┴──────────────────┴──────────────────────────────┘
```

## Project layout

```
le-mans-ultimate-telemetry/
├── server.py                 # FastAPI + ctypes SMMP reader + WebSocket stream
├── static/
│   └── index.html            # Monolithic frontend (HTML + CSS + JS)
├── .gitignore
├── LICENSE
└── README.md
```

## Tuning

| Variable | Where | Default | What it controls |
|---|---|---|---|
| `STREAM_HZ` (CLI `--hz`) | `server.py` | 50 | WebSocket frame rate |
| `tireColor()` thresholds | `index.html` JS | 60 / 80–100 / 115 °C | Cold / optimal / hot tire colour bands |
| `fuel low / critical` | `index.html` JS | 25 / 10 L | When the FUEL card turns orange / blinks red |
| `ovh` threshold | `index.html` JS | water > 110 °C or oil > 130 °C | Activates OVHEAT flag |

## Troubleshooting

- **"AWAITING TELEMETRY" stays forever** — the SMMP plugin DLL is not loaded.
  Verify the path (`<LMU>\Bin64\Plugins\`) and that LMU has loaded it
  (`Settings → Plugins` checkbox).
- **`ModuleNotFoundError: No module named 'fastapi'`** — activate your venv,
  then `pip install fastapi "uvicorn[standard]"`.
- **Strategist gets "Mixed Content" error** — ngrok HTTPS forwards to plain
  HTTP backend, this is normal; the frontend uses `wss://` so it works.
- **Wrong tire temperatures** — values are converted from Kelvin in
  `_wheel_to_dict()`. Double-check `KELVIN` constant if your readings look
  off by ~273°.

## Credits

- Shared memory layout based on [rF2SharedMemoryMapPlugin](https://github.com/TheIronWolfMod/rF2SharedMemoryMapPlugin) by TheIronWolfMod
- Visual language inspired by FIA WEC / F1 pit-wall engineer dashboards
