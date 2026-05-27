> 🌐 **Language:** **English** · [Русский](STRATEGIST_GUIDE.ru.md)

# LMU PIT WALL — Strategist Guide

This is the operating manual for the **race engineer / strategist** watching a Le Mans Ultimate driver remotely. No technical background required. Read it once — then keep it as a cheat sheet.

---

## What you need before the start

- **Large monitor** — 1920×1080 minimum, 2560×1440 ideal. The dashboard is designed around a 4-column layout.
- **Browser: Chrome or Edge** (Firefox works too). Safari is not recommended — WebSocket issues.
- **Headset** — voice link with the driver (Discord, TeamSpeak — whatever you agreed on).
- **Stable internet** — the stream is constant but light (~10 KB/s).
- **Link from the driver** — two parts:
  - Base URL: `https://lmu-pitwall.pages.dev/`
  - WebSocket parameter: `?ws=wss://<...>.trycloudflare.com/ws` or `?ws=wss://<...>.ngrok-free.app/ws`

The driver sends you the **complete glued URL**. Looks like this:

```
https://lmu-pitwall.pages.dev/?ws=wss://bean-turbo-paxil-wal.trycloudflare.com/ws
```

---

## Connect in 3 steps

### 1. Open the link
Open it in the browser. After 1-2 seconds the dashboard should load. Black screen? Reload (`Ctrl+R`).

### 2. Check the connection
Bottom-left of the footer — status LED:
- 🟢 **CONNECTED** — link to the driver's server is established.
- 🔴 **DISCONNECTED** — no link. Check your internet, then ping the driver: is their cloudflared/ngrok window alive?

If a yellow **AWAITING TELEMETRY** modal sits over the dashboard → the driver is in the garage or menu. Normal — data will flow the moment they go on track.

### 3. F11 — full-screen mode
Press **F11** right away — the browser drops its chrome and the dashboard fills the monitor. Exit: F11 again.

### If the URL gets stale (driver's tunnel restarted)
Bottom-right of the footer: **⚙ CONNECTION** button. Click it — a modal opens with a field for the new WebSocket URL. Paste the new address from the driver, click **SAVE & RECONNECT**. It's remembered in your browser; next time you don't need to enter it.

---

## Screen layout

```
┌────────────────────────────────────────────────────────────────────┐
│  [HEADER] ● track · car · driver · class · weather · session clock │
├──────────────┬─────────────┬──────────────┬────────────────────────┤
│              │             │              │                        │
│   VITALS     │   TIRES     │   TIMING     │   CLASSES + MAP        │
│  (live, 60   │ (4 wheels,  │ (position,   │ (whole field, live     │
│   per sec)   │  temps,     │  lap times,  │  track map)            │
│              │  pressures) │  sectors)    │                        │
│              ├─────────────┤              │                        │
│              │  CAR STATUS │              │                        │
│              │ (fuel,      │              │                        │
│              │  water, oil)│              │                        │
└──────────────┴─────────────┴──────────────┴────────────────────────┘
│  [FOOTER] status · latency · ⚙ CONNECTION · ? GUIDE · local time   │
└────────────────────────────────────────────────────────────────────┘
```

---

## What every block means

### Column 1 — VITALS (driver's steering wheel view)

| Element | What it means | When to call it |
|---|---|---|
| **Huge gear digit** | Current gear (R = reverse, N = neutral) | If N on track for long — something broke |
| **RPM bar** (20 shift-lights) | Engine RPM. Green → yellow → red | All red + blinking — driver near rev limiter |
| **THROTTLE / BRAKE / CLUTCH** (vertical bars) | Real-time pedal % | Reveals braking pattern: early/late/trail-braking |
| **STEERING** (centered bar) | Steering position. Cyan right = turning right | Watch for input corrections in long corners |
| **SPD KM/H** | Speed in km/h | Top speed on straights; compare with rivals |

### Column 2 — TIRES

4 squares: FL (front-left), FR (front-right), RL (rear-left), RR (rear-right).

**Tire colour** = carcass temperature:

| Colour | Range | Meaning |
|---|---|---|
| 🟦 Dark blue | < 60 °C | Cold, no grip — DO NOT ATTACK |
| 🔵 Blue | 60-80 °C | Warming up, building grip |
| 🟢 Green | 80-100 °C | **Optimum** — peak grip |
| 🟡 Yellow | 100-115 °C | On the limit, careful |
| 🔴 Red | > 115 °C | **Overheat** — degradation, blistering risk |

Colours within a single tire (3 segments) show distribution: inner / center / outer shoulder. Inner edge red and outer green = too much camber or aggressive one-way corners.

Below the temperature digit, small data:
- **PSI** — pressure, in psi and kPa.
- **BRK** — brake disc temperature (> 600 °C is critical for GT3).
- **CARCASS** — carcass temperature (rises slowly, matters for long stints).
- **WEAR** — % of tread remaining. 100% = new, 0% = down to the cords.

**When to radio the driver:**
- All 4 red → "Manage tires, lift early."
- Just one red (usually rear) → "You're sliding the rear, smooth throttle."
- All 4 blue after 3 laps → "Push harder, tires not up to temp."
- Wear < 30% with 10 laps to go in the stint → "Shave for 3 laps then box."

### Column 3 — CAR STATUS (fuel, temps)

- **FUEL** — litres in the tank. Colour:
  - 🟡 Yellow — normal.
  - 🟠 Orange — under 25 L (~5 laps on a Hypercar).
  - 🔴 Red + blinking — under 10 L, **urgent pit**.
- Below: `XX.X L/LAP · YY LEFT` — fuel per lap (auto-averaged over the last 5 laps) and laps remaining.
- **WATER** — coolant temperature. Normal < 100 °C. Yellow 100-108 °C. Red > 108 °C — engine on the edge.
- **OIL** — oil temperature. Normal < 120 °C. Red > 130 °C — pit immediately.
- **STINT** — laps done in the current stint.
- Top-right: **TURBO X.XX BAR** — boost pressure (LMDh/LMH).

### Column 4 — TIMING TOWER

#### Top: POSITION + LAP
Giant digits. If **POSITION is yellow** — the driver is in pit lane.

#### LAP TIMES
| Colour | Meaning |
|---|---|
| Cyan | Current lap, running up |
| White | Last completed lap |
| **Magenta** | Best lap of the session |

Under the best: `LAST Δ +0.342` — how much the last lap was worse/better than the best.

#### SECTORS (4 rows × 3 sectors)
| Row | What |
|---|---|
| CUR | Current sector times (fill in as the lap progresses) |
| LAST | Last completed lap split by sector |
| BEST | Best lap split by sector (magenta) |
| DELTA | LAST minus BEST. Green = improved, magenta = lost |

The active sector (where the driver is right now) is highlighted cyan.

#### GAPS
- **AHEAD** — gap to the car in front (negative, green).
- **LEADER** — gap behind the overall leader (negative, green).
- **STOPS** — number of pit stops the driver has made.
- **PEN** — penalties.

#### FLAG STRIP (bottom of the tower)
Lights = current warnings:
- **LIVE** — data flowing (green = all good).
- **PIT** — driver in pit lane.
- **SPDLIM** — pit lane speed limiter active.
- **OVHEAT** — engine overheating (red blink).
- **FUEL!** — critically low fuel.
- **YELLOW** — yellow flag on track.

### Column 5 — CLASSES + MAP (multiclass)

#### CLASSES (full field table)

Cars bucketed by class with colour swatches:
- 🔴 **Hypercar** / LMH / LMDh
- 🔵 **LMP2** / LMP3
- 🟡 **LMGT3** / GT3 / GTE

Within each group, rows show:
```
[swatch] [overall place] [P in class] [DRIVER NAME] [+gap]
```

- Your car — **cyan-highlighted row**.
- If a rival has a `PIT` tag in amber — they're in the pit lane (opportunity to gain a position).
- Gaps: `LEAD` for class leader, `+12.3` seconds to leader, `+1L` if lapped.

#### TRACK MAP

Canvas at the bottom. The grey line is the track outline. The dots are every car live, coloured by class. **Your driver = cyan crosshair in the center of a targeting ring.**

- First 30-90 seconds of the race the map is incomplete — it builds from the driver's positions. After the first complete lap, the outline closes and is stable thereafter.
- A rival in the pits — dot dims and gets an amber outline.
- Bottom: track length, points in the trail, driver's current lap %.

---

## 7 typical scenarios — what to radio

### Scenario 1: Running out of fuel
- FUEL turned orange → **"Pit window opens in 3 laps, fuel."**
- FUEL red + blinking → **"Box this lap, fuel critical."**

### Scenario 2: Tires degrading
- All 4 tires yellow / WEAR below 40% → **"Manage tires, save the rears."**
- Red → **"Tires gone, box for fresh set."**

### Scenario 3: Engine overheating
- WATER > 105 °C → **"Engine hot, lift on straights for 1 lap."**
- OIL > 130 °C → **"Box immediately, oil critical."**

### Scenario 4: Faster class catching (blue flag situation)
- On the MAP you see a red dot (Hypercar) approaching the yellow one (your GT3) from behind → **"Hypercar 5 seconds behind, blue flag coming."**
- After they pass → **"Track ahead is clear, push."**

### Scenario 5: Direct rival pits
- In CLASSES the adjacent row shows a PIT tag → **"P2 in pit, you gain position next sector."**

### Scenario 6: Lap-time trend dropping
- LAP TIMES: BEST 1:32.4, LAST 1:33.8 for 3 laps running → **"Pace dropping 1.4 sec, tires going, plan stop in 4 laps."**

### Scenario 7: Pit window
- From FUEL_LEFT and lap_count work out: "how many laps until refuel". The dashboard already shows this under FUEL as `YY LEFT`. When the window approaches → **"Pit window: 6-8 laps to box."**

---

## If something breaks

| What you see | What it means | What to do |
|---|---|---|
| Yellow modal **AWAITING TELEMETRY** | Driver in garage / menu / loading | Normal, wait. If > 5 minutes — ask the driver. |
| Red modal **AWAITING SERVER** | No link to the driver's server | Ask the driver — is their cloudflared/ngrok window alive? Possibly restart. |
| Red **CANNOT REACH SERVER** | URL is stale (driver restarted tunnel) | Click ⚙ → enter new WS URL → SAVE. |
| Latency stuck at `0 ms` | Connection frozen | Refresh: `Ctrl+R`. |
| Digits frozen, not changing | Server crashed | Driver must restart `server.py`. |
| Map empty | Driver just went on track | Wait one lap, it will draw itself. |

---

## Hotkeys

| Key | Action |
|---|---|
| **F11** | Toggle full-screen |
| **Ctrl + R** | Reload the page |
| **Ctrl + Shift + R** | Hard reload (if the UI looks broken) |
| **Esc** | Close any modal |
| **?** | Open this guide |
| **⚙ CONNECTION** (footer button) | Set WS URL |
| **? GUIDE** (footer button) | Open this guide |

---

## What to write down (paper pit journal)

The dashboard shows live data but does not store history. A good strategist also logs by hand:

| LAP | LAP TIME | FUEL after lap | TIRE TEMP avg | NOTES |
|----:|:--------:|:---------------:|:-------------:|:------|
|  47 | 3:31.124 |      72.4 L     |     92 °C     | First lap of stint |
|  48 | 3:30.612 |      69.8 L     |     96 °C     | Sector 2 +0.3 vs best |
|  49 | 3:30.998 |      67.2 L     |     98 °C     | Yellow at Tertre |

This gives you a sense of stint trend across upcoming stints and tire changes.

---

Good luck out there!

— LMU Pit Wall
