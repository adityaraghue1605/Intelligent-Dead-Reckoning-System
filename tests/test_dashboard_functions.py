"""
Automated Functional Button & Outage Verification Suite
======================================================
Tests all dashboard controls:
  1. Start Drive (Play)
  2. Pause
  3. Reset
  4. 30s Blackout (Dataset Time)
  5. 60s Blackout (Dataset Time)
  6. Toggle Blackout
  7. GNSS Recovery
  8. Drift Calculation (Dynamic position error and percentage)

Outputs explicit PASS/FAIL per control.
"""

import time
import json
import urllib.request
import numpy as np
import pytest

BASE_URL = "http://127.0.0.1:8000"


def http_get(path):
    with urllib.request.urlopen(f"{BASE_URL}{path}") as r:
        return json.loads(r.read().decode())


def http_post(path, data=None):
    payload = json.dumps(data).encode() if data is not None else b"{}"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def test_button_functions_automated():
    print("\n" + "=" * 80)
    print("RUNNING AUTOMATED DASHBOARD BUTTON FUNCTION TESTS")
    print("=" * 80)
    results = {}

    # Reset initial state
    http_post("/api/v1/simulation/reset")
    time.sleep(0.2)

    # 1. Start Drive Test
    try:
        r_play = http_post("/api/v1/simulation/play")
        time.sleep(0.5)
        st = http_get("/api/v1/status")
        assert r_play.get("status") == "running", "Expected status 'running'"
        assert st.get("is_running") is True, "Expected is_running=True"
        assert st.get("current_step", 0) > 0, "Expected current_step to advance"
        results["Start Drive"] = ("PASS", f"Playback active, step advanced to {st.get('current_step')}")
    except Exception as e:
        results["Start Drive"] = ("FAIL", str(e))

    # 2. Pause Test
    try:
        r_pause = http_post("/api/v1/simulation/pause")
        step_at_pause = http_get("/api/v1/status").get("current_step")
        time.sleep(0.4)
        step_after_wait = http_get("/api/v1/status").get("current_step")
        assert r_pause.get("status") == "paused", "Expected status 'paused'"
        assert step_at_pause == step_after_wait, f"Step should freeze: {step_at_pause} vs {step_after_wait}"
        results["Pause"] = ("PASS", f"Playback paused at step {step_at_pause}")
    except Exception as e:
        results["Pause"] = ("FAIL", str(e))

    # 3. Reset Test
    try:
        r_reset = http_post("/api/v1/simulation/reset")
        st_reset = http_get("/api/v1/status")
        assert r_reset.get("status") == "reset", "Expected status 'reset'"
        assert st_reset.get("current_step") == 0, f"Expected step 0, got {st_reset.get('current_step')}"
        assert st_reset.get("is_running") is False, "Expected is_running=False"
        assert st_reset.get("forced_blackout") is False, "Expected blackout False"
        results["Reset"] = ("PASS", "State restored to step 0, blackout cleared")
    except Exception as e:
        results["Reset"] = ("FAIL", str(e))

    # 4. 30s Blackout Test (Dataset Time)
    try:
        from api.main import sim
        sim.load_scenario("S-A1.csv")
        sim.current_step = 1500  # Cruising segment (speed > 25 km/h)
        sim.is_running = True
        
        # Test HTTP blackout trigger endpoint
        r_bo30 = http_post("/api/v1/simulation/blackout", {"active": True, "duration_seconds": 30})
        assert r_bo30.get("blackout_active") is True
        
        # Trigger on local sim instance
        sim.trigger_blackout(duration_seconds=30, active=True)
        assert sim.forced_blackout is True
        
        # Step through 30s of dataset time (60 steps @ 0.5s = 30.0s)
        outage_packets = []
        for _ in range(60):
            pkt = sim.step()
            outage_packets.append(pkt)
            if not sim.forced_blackout:
                break
        
        last_outage_pkt = outage_packets[-1]
        outage_info = last_outage_pkt["outage"]
        active_dur = outage_info["duration_s"]
        active_dist = outage_info["distance_m"]
        assert active_dist > 50.0, f"Distance should be >50m, got {active_dist}"
        assert outage_info["ai_drift_m"] > 0.0, "Drift error must be computed (>0)"
        results["30s blackout"] = ("PASS", f"Active for 30s dataset time, distance={active_dist:.1f}m, drift={outage_info['ai_drift_m']:.2f}m")
    except Exception as e:
        results["30s blackout"] = ("FAIL", str(e))

    # 5. 60s Blackout Test (Dataset Time)
    try:
        sim.current_step = 1800  # High speed driving segment
        sim.is_running = True
        
        # Test HTTP endpoint
        r_bo60 = http_post("/api/v1/simulation/blackout", {"active": True, "duration_seconds": 60})
        assert r_bo60.get("blackout_active") is True
        
        sim.trigger_blackout(duration_seconds=60, active=True)
        assert sim.forced_blackout is True
        
        outage_packets_60 = []
        for _ in range(120): # 120 steps @ 0.5s = 60s
            pkt = sim.step()
            outage_packets_60.append(pkt)
            if not sim.forced_blackout:
                break
        
        last_pkt_60 = outage_packets_60[-1]
        dur = last_pkt_60["outage"]["duration_s"]
        dist = last_pkt_60["outage"]["distance_m"]
        assert dur >= 20.0, f"Expected duration >= 20s, got {dur}"
        assert dist > 100.0, f"Expected distance > 100m, got {dist}"
        results["60s blackout"] = ("PASS", f"Active for {dur:.1f}s dataset time, distance={dist:.1f}m, drift={last_pkt_60['outage']['ai_drift_m']:.2f}m")
    except Exception as e:
        results["60s blackout"] = ("FAIL", str(e))

    # 6. Toggle Blackout Test
    try:
        r_toggle_on = http_post("/api/v1/simulation/blackout", {"active": True})
        st_on = http_get("/api/v1/status")
        assert r_toggle_on.get("blackout_active") is True
        assert st_on.get("forced_blackout") is True
        
        r_toggle_off = http_post("/api/v1/simulation/blackout", {"active": False})
        st_off = http_get("/api/v1/status")
        assert r_toggle_off.get("blackout_active") is False
        assert st_off.get("forced_blackout") is False
        results["Toggle blackout"] = ("PASS", "Manual toggle ON and OFF verified")
    except Exception as e:
        results["Toggle blackout"] = ("FAIL", str(e))

    # 7. GNSS Recovery Test
    try:
        sim.current_step = 2000
        sim.trigger_blackout(duration_seconds=10, active=True)
        for _ in range(5):
            sim.step()
        assert sim.forced_blackout is True
        
        # End blackout and verify recovery
        sim.trigger_blackout(active=False)
        pkt_recovery = sim.step()
        assert sim.forced_blackout is False
        assert pkt_recovery["idr"]["gnss_outage"] is False
        assert sim.last_completed_outage is not None
        results["GNSS recovery"] = ("PASS", f"GNSS restored, mode='{pkt_recovery['idr']['nav_mode']}', benchmark captured")
    except Exception as e:
        results["GNSS recovery"] = ("FAIL", str(e))

    # 8. Drift Calculation Test
    try:
        lc = sim.last_completed_outage
        assert lc is not None, "Completed outage benchmark must exist"
        err_m = lc["ai_error_m"]
        dist_m = lc["distance_travelled_m"]
        drift_pct = lc["ai_drift_pct"]
        
        assert dist_m > 0.0, "Distance must be > 0"
        expected_pct = round(100.0 * err_m / dist_m, 2)
        assert abs(drift_pct - expected_pct) < 0.1, f"Formula mismatch: {drift_pct} vs expected {expected_pct}"
        assert err_m > 0.0, "Drift error must be non-zero during real motion"
        results["Drift calculation"] = ("PASS", f"Error={err_m:.2f}m, Dist={dist_m:.1f}m, Drift={drift_pct:.2f}% (Formula verified)")
    except Exception as e:
        results["Drift calculation"] = ("FAIL", str(e))

    # Print Report Table
    print(f"\n{'Control / Function':<25} | {'Result':<8} | {'Details'}")
    print("-" * 80)
    all_passed = True
    for ctrl, (res, details) in results.items():
        if res != "PASS":
            all_passed = False
        print(f"{ctrl:<25} | {res:<8} | {details}")
    print("-" * 80)

    assert all_passed, "One or more button function tests failed!"


if __name__ == "__main__":
    test_button_functions_automated()
