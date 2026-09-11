"""
Unit Tests for Strapdown Inertial Navigation System (SINS)
"""

import math
import numpy as np
import pytest
from navigation.ins import (
    StrapdownINS, geodetic_to_ecef, ecef_to_geodetic,
    geodetic_to_enu, enu_to_geodetic, euler_to_rotation_matrix
)


def test_wgs84_round_trip():
    """Verify conversion to ECEF and back to WGS84 preserves coordinates."""
    lat_in, lon_in, alt_in = 52.43163, -1.525745, 120.0
    x, y, z = geodetic_to_ecef(lat_in, lon_in, alt_in)
    lat_out, lon_out, alt_out = ecef_to_geodetic(x, y, z)

    assert abs(lat_out - lat_in) < 1e-7
    assert abs(lon_out - lon_in) < 1e-7
    assert abs(alt_out - alt_in) < 1e-3


def test_enu_round_trip():
    """Verify local ENU conversion and inverse back to Lat/Lon."""
    ref_lat, ref_lon, ref_alt = 52.0, -1.5, 50.0
    target_lat, target_lon, target_alt = 52.01, -1.49, 55.0

    enu = geodetic_to_enu(target_lat, target_lon, target_alt, ref_lat, ref_lon, ref_alt)
    assert len(enu) == 3
    # Moving north and east should yield positive e and n
    assert enu[0] > 0.0 # East
    assert enu[1] > 0.0 # North

    lat_back, lon_back, alt_back = enu_to_geodetic(enu[0], enu[1], enu[2], ref_lat, ref_lon, ref_alt)
    assert abs(lat_back - target_lat) < 1e-7
    assert abs(lon_back - target_lon) < 1e-7


def test_ins_stationary_gravity_compensation():
    """Stationary phone with pure 1g downward should yield near-zero vertical acceleration."""
    ins = StrapdownINS(52.43163, -1.525745, 100.0)
    ins.initialize_state(52.43163, -1.525745, 100.0, heading_deg=0.0, speed_mps=0.0)

    # Smartphone resting flat: Accel Z = +9.80665 m/s^2
    acc_body = np.array([0.0, 0.0, 9.80665], dtype=np.float64)
    res = ins.step(acc_body, dt=0.5)

    # Velocity and position drift should be essentially zero
    assert res["speed_mps"] < 0.05
    assert abs(res["pos_enu"][2]) < 0.05
