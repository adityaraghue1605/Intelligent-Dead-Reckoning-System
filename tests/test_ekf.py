"""
Unit Tests for Error-State Extended Kalman Filter & IDR Engine
"""

import math
import numpy as np
import pytest
from navigation.ekf import ErrorStateEKF
from navigation.nhc import NonHolonomicConstraints
from idr_engine import IDREngine, SensorPacket


def test_ekf_initialization_and_predict():
    """Verify EKF covariance propagation during prediction step."""
    ekf = ErrorStateEKF(52.43163, -1.525745, 100.0)
    ekf.ins.initialize_state(52.43163, -1.525745, 100.0, heading_deg=90.0, speed_mps=10.0)

    p_initial = ekf.P[0, 0]
    # Predict step
    acc_raw = np.array([0.0, 0.0, 9.80665])
    ekf.predict(acc_raw, dt=0.5)

    # Uncertainty should increase without measurement update
    assert ekf.P[0, 0] >= p_initial


def test_ekf_gnss_update():
    """Verify GNSS position update contracts position uncertainty."""
    ekf = ErrorStateEKF(52.43163, -1.525745, 100.0)
    ekf.ins.initialize_state(52.43163, -1.525745, 100.0, heading_deg=90.0, speed_mps=10.0)

    # Let uncertainty grow
    for _ in range(5):
        ekf.predict(np.array([0.0, 0.0, 9.80665]), dt=0.5)

    p_before = ekf.P[0, 0]
    # Apply GNSS update
    ekf.update_gnss(52.431635, -1.525740, 100.0, speed_mps=10.0, course_deg=90.0, accuracy_m=2.0)
    p_after = ekf.P[0, 0]

    assert p_after < p_before


def test_idr_blackout_drift_benchmark():
    """
    Simulate a vehicle driving forward at 20 m/s (~72 km/h) for 60 seconds of GNSS blackout.
    With AI Speed + NHC constraints, drift MUST be < 10% of distance traveled.
    Distance traveled = 20 m/s * 60s = 1,200 meters.
    10% target = max 120 meters drift.
    """
    engine = IDREngine()
    start_lat = 52.43163
    start_lon = -1.525745
    lat_curr = start_lat
    lon_curr = start_lon
    steps = 120  # 60s at 2 Hz

    # 1. Warm-up phase: 10 steps with active GNSS fix at 20 m/s East and realistic road vibrations
    for step in range(10):
        t_ms = step * 500.0
        lon_curr += (10.0 / (111319.5 * math.cos(math.radians(lat_curr))))
        vibe_x = float(np.sin(step * 0.4) * 0.3)
        vibe_y = float(np.cos(step * 0.5) * 0.3)
        packet = SensorPacket(
            timestamp_ms=t_ms,
            accel=[vibe_x, vibe_y, 9.80665],
            gyro=[0.0, 0.0, 0.0],
            orientation=[90.0, 0.0, 0.0],
            gnss_lat=lat_curr,
            gnss_lon=lon_curr,
            gnss_speed_kmh=72.0,
            gnss_bearing_deg=90.0
        )
        engine.process_sensor_packet(packet)

    # 2. Trigger 60s GNSS Blackout
    engine.trigger_simulated_blackout(True)

    for step in range(10, 10 + steps):
        t_ms = step * 500.0
        lon_curr += (10.0 / (111319.5 * math.cos(math.radians(lat_curr))))

        # Typical road vibration at 20 m/s
        vibe_x = float(np.sin(step * 0.4) * 0.3)
        vibe_y = float(np.cos(step * 0.5) * 0.3)

        packet = SensorPacket(
            timestamp_ms=t_ms,
            accel=[vibe_x, vibe_y, 9.80665],
            gyro=[0.0, 0.0, 0.0],
            orientation=[90.0, 0.0, 0.0],
            gnss_lat=lat_curr, # Provided to engine so it can compute drift against GT
            gnss_lon=lon_curr,
            gnss_speed_kmh=72.0,
            gnss_bearing_deg=90.0
        )

        res = engine.process_sensor_packet(packet)

    # Verify drift percentage meets target
    print(f"\nFinal Outage Distance: {res.outage_distance_m:.1f} m")
    print(f"Final Drift Error: {res.drift_error_m:.2f} m")
    print(f"Final Drift Percentage: {res.drift_percentage:.2f}%")

    assert res.gnss_outage is True
    assert res.drift_percentage < 10.0, f"Drift {res.drift_percentage}% exceeds 10% target!"
