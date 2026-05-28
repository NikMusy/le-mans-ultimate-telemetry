"""
supervisor.py — keeps the whole LMU Pit Wall stack alive, automatically.
============================================================================

Run this once (or let the Scheduled Task run it at login) and forget about it.

What it does, forever, in a loop:
  1. Starts cloudflared (quick tunnel) and captures its public URL.
  2. Whenever that URL changes, writes static/ws-config.json AND deploys it
     to Cloudflare Pages — so the strategist's PERMANENT page
     (https://lmu-pitwall.pages.dev/) auto-discovers the live tunnel. The
     strategist never needs a new link.
  3. Starts server.py (LIVE telemetry) and keeps it alive.
  4. Watches Le Mans Ultimate: when the game (re)starts, it restarts
     server.py so it cleanly re-attaches to the new session's shared memory.
  5. Restarts anything that dies. Logs everything to supervisor.log.

Stop it: close the window, or run  stop-pitwall.bat  (kills cloudflared +
python). Start it: run  start-pitwall.bat  or the Scheduled Task.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# ------------------------------------------------------------------
# Paths / config
# ------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
SERVER = str(ROOT / "server.py")
STATIC = ROOT / "static"
WS_CONFIG = STATIC / "ws-config.json"
LOG = ROOT / "supervisor.log"
PORT = 8000
POLL_SECONDS = 5
LMU_IMAGE = "Le Mans Ultimate.exe"


def find_cloudflared() -> str:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if base.exists():
        for p in base.glob("Cloudflare.cloudflared_*/cloudflared.exe"):
            return str(p)
    return "cloudflared"


CLOUDFLARED = find_cloudflared()
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def log(msg: str):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


state = {
    "tunnel_proc": None,
    "server_proc": None,
    "tunnel_ws": None,      # current wss://.../ws
    "last_lmu_pid": None,
    "deploying": False,
}


# ------------------------------------------------------------------
# Cloudflare Pages publish (so the permanent page finds the live tunnel)
# ------------------------------------------------------------------
def publish_config(ws_url: str):
    cfg = {
        "ws": ws_url,
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "note": "Auto-published by supervisor.py. Do not edit by hand.",
    }
    try:
        WS_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        log(f"ws-config.json -> {ws_url}")
    except Exception as e:
        log(f"config write error: {e}")

    if state["deploying"]:
        log("deploy already in progress, skipping")
        return

    def _deploy():
        state["deploying"] = True
        try:
            r = subprocess.run(
                "npm run deploy",
                cwd=str(ROOT), capture_output=True, text=True,
                timeout=180, shell=True,
            )
            if r.returncode == 0:
                log("Deployed ws-config to Cloudflare Pages OK")
            else:
                tail = (r.stderr or r.stdout or "")[-300:]
                log(f"deploy failed rc={r.returncode}: {tail}")
        except subprocess.TimeoutExpired:
            log("deploy timed out")
        except Exception as e:
            log(f"deploy error: {e}")
        finally:
            state["deploying"] = False

    threading.Thread(target=_deploy, daemon=True).start()


# ------------------------------------------------------------------
# Tunnel
# ------------------------------------------------------------------
def _tunnel_reader(proc):
    try:
        for raw in iter(proc.stdout.readline, ""):
            if not raw:
                break
            m = URL_RE.search(raw)
            if m:
                ws = "wss://" + m.group(0)[len("https://"):] + "/ws"
                if ws != state["tunnel_ws"]:
                    state["tunnel_ws"] = ws
                    log(f"Tunnel up: {m.group(0)}")
                    publish_config(ws)
    except Exception as e:
        log(f"tunnel reader error: {e}")


def start_tunnel():
    log("Starting cloudflared tunnel...")
    try:
        proc = subprocess.Popen(
            [CLOUDFLARED, "tunnel", "--url", f"http://localhost:{PORT}",
             "--no-autoupdate", "--protocol", "http2"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except Exception as e:
        log(f"tunnel start failed: {e}")
        return
    state["tunnel_proc"] = proc
    threading.Thread(target=_tunnel_reader, args=(proc,), daemon=True).start()


# ------------------------------------------------------------------
# Telemetry server
# ------------------------------------------------------------------
def start_server():
    log("Starting server.py (LIVE)...")
    try:
        proc = subprocess.Popen(
            [PYTHON, SERVER],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        state["server_proc"] = proc
    except Exception as e:
        log(f"server start failed: {e}")


def stop_server():
    p = state.get("server_proc")
    if p and p.poll() is None:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    state["server_proc"] = None


def restart_server():
    stop_server()
    time.sleep(1)
    start_server()


# ------------------------------------------------------------------
# Le Mans Ultimate watch
# ------------------------------------------------------------------
def find_lmu_pid():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {LMU_IMAGE}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        m = re.search(r'"' + re.escape(LMU_IMAGE) + r'","(\d+)"', out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def kill_stray_tunnels():
    # Avoid duplicate tunnels if a previous supervisor left one behind.
    try:
        subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"],
                       capture_output=True, text=True)
    except Exception:
        pass


# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------
def main():
    log("=" * 60)
    log("LMU Pit Wall supervisor starting")
    log(f"root        : {ROOT}")
    log(f"python      : {PYTHON}")
    log(f"cloudflared : {CLOUDFLARED}")

    kill_stray_tunnels()
    time.sleep(1)
    start_tunnel()
    start_server()
    state["last_lmu_pid"] = find_lmu_pid()
    log(f"LMU running : {bool(state['last_lmu_pid'])}")
    log("Supervisor is now babysitting the stack. Leave this window open.")

    while True:
        time.sleep(POLL_SECONDS)

        # 1) tunnel alive?
        tp = state["tunnel_proc"]
        if tp is None or tp.poll() is not None:
            log("Tunnel not running -> restarting")
            start_tunnel()

        # 2) server alive?
        sp = state["server_proc"]
        if sp is None or sp.poll() is not None:
            log("server.py not running -> restarting")
            start_server()

        # 3) LMU (re)start -> clean server reattach
        pid = find_lmu_pid()
        if pid != state["last_lmu_pid"]:
            if pid:
                log(f"LMU (re)started (PID {pid}) -> restarting server for clean attach")
                restart_server()
            else:
                log("LMU closed (server stays up, will show 'waiting')")
            state["last_lmu_pid"] = pid


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Supervisor stopped by user")
    except Exception as e:
        log(f"Supervisor crashed: {e}")
        raise
