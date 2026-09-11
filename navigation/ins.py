"""
Strapdown Inertial Navigation System (SINS) Mechanization
=========================================================
Implements the core 3D kinematics integration for inertial navigation:
 - Attitude integration via quaternion or rotation matrix
 - Specific force frame rotation (Body frame -> Navigation ENU frame)
 - Gravity compensation
 - Trapezoidal velocity and position numerical integration
 - Geodetic (WGS84 Lat/Lon/Alt) <-> Local Cartesian (East-North-Up) conversion
"""

import math
import numpy as np
from typing import Tuple, Optional, Dict, Any

# WGS84 ellipsoid constants
WGS84_A = 6378137.0          # Semi-major axis (meters)
WGS84_F = 1.0 / 298.257223563  # Flattening
WGS84_B = WGS84_A * (1.0 - WGS84_F)  # Semi-minor axis
WGS84_E2 = WGS84_F * (2.0 - WGS84_F) # First eccentricity squared
GRAVITY_STANDARD = 9.80665   # Standard gravity (m/s^2)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> np.ndarray:
    """Convert WGS84 Geodetic coordinates (lat, lon, alt) to Earth-Centered Earth-Fixed (ECEF)."""
    phi = math.radians(lat_deg)
    lam = math.radians(lon_deg)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    sin_lam = math.sin(lam)
    cos_lam = math.cos(lam)

    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_phi * sin_phi)
    x = (n + alt_m) * cos_phi * cos_lam
    y = (n + alt_m) * cos_phi * sin_lam
    z = (n * (1.0 - WGS84_E2) + alt_m) * sin_phi
    return np.array([x, y, z], dtype=np.float64)


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Convert ECEF coordinates (x, y, z) to WGS84 Geodetic coordinates (lat, lon, alt)."""
    p = math.sqrt(x * x + y * y)
    if p < 1e-6:
        lat = 90.0 if z > 0 else -90.0
        return lat, 0.0, abs(z) - WGS84_B

    theta = math.atan2(z * WGS84_A, p * WGS84_B)
    sin_theta = math.sin(theta)
    cos_theta = math.cos(theta)

    e_prime_sq = (WGS84_A**2 - WGS84_B**2) / (WGS84_B**2)
    phi = math.atan2(
        z + e_prime_sq * WGS84_B * (sin_theta**3),
        p - WGS84_E2 * WGS84_A * (cos_theta**3)
    )
    lam = math.atan2(y, x)

    sin_phi = math.sin(phi)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_phi * sin_phi)
    alt = p / math.cos(phi) - n

    return math.degrees(phi), math.degrees(lam), alt


def geodetic_to_enu(lat_deg: float, lon_deg: float, alt_m: float,
                    ref_lat_deg: float, ref_lon_deg: float, ref_alt_m: float) -> np.ndarray:
    """Convert WGS84 Geodetic point to local East-North-Up (ENU) coordinates relative to reference origin."""
    xyz = geodetic_to_ecef(lat_deg, lon_deg, alt_m)
    xyz_ref = geodetic_to_ecef(ref_lat_deg, ref_lon_deg, ref_alt_m)
    dxyz = xyz - xyz_ref

    phi = math.radians(ref_lat_deg)
    lam = math.radians(ref_lon_deg)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    sin_lam = math.sin(lam)
    cos_lam = math.cos(lam)

    # Rotation matrix from ECEF to ENU
    e = -sin_lam * dxyz[0] + cos_lam * dxyz[1]
    n = -sin_phi * cos_lam * dxyz[0] - sin_phi * sin_lam * dxyz[1] + cos_phi * dxyz[2]
    u =  cos_phi * cos_lam * dxyz[0] + cos_phi * sin_lam * dxyz[1] + sin_phi * dxyz[2]
    return np.array([e, n, u], dtype=np.float64)


def enu_to_geodetic(e: float, n: float, u: float,
                     ref_lat_deg: float, ref_lon_deg: float, ref_alt_m: float) -> Tuple[float, float, float]:
    """Convert local ENU coordinates back to WGS84 Geodetic point (lat, lon, alt)."""
    phi = math.radians(ref_lat_deg)
    lam = math.radians(ref_lon_deg)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    sin_lam = math.sin(lam)
    cos_lam = math.cos(lam)

    # Rotation matrix from ENU to ECEF (transpose of ECEF to ENU)
    dx = -sin_lam * e - sin_phi * cos_lam * n + cos_phi * cos_lam * u
    dy =  cos_lam * e - sin_phi * sin_lam * n + cos_phi * sin_lam * u
    dz =  cos_phi * n + sin_phi * u

    xyz_ref = geodetic_to_ecef(ref_lat_deg, ref_lon_deg, ref_alt_m)
    xyz = xyz_ref + np.array([dx, dy, dz], dtype=np.float64)
    return ecef_to_geodetic(xyz[0], xyz[1], xyz[2])


def euler_to_rotation_matrix(roll_rad: float, pitch_rad: float, yaw_rad: float) -> np.ndarray:
    """
    Construct rotation matrix R_b^n from Body to Navigation (ENU) frame:
    Roll (phi, around X), Pitch (theta, around Y), Yaw (psi, around Z, where 0=East, 90=North)
    """
    cr = math.cos(roll_rad)
    sr = math.sin(roll_rad)
    cp = math.cos(pitch_rad)
    sp = math.sin(pitch_rad)
    cy = math.cos(yaw_rad)
    sy = math.sin(yaw_rad)

    # Rz(yaw) * Ry(pitch) * Rx(roll)
    R = np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp,     cp * sr,                cp * cr]
    ], dtype=np.float64)
    return R


def rotation_matrix_to_euler(R: np.ndarray) -> Tuple[float, float, float]:
    """Extract Roll, Pitch, Yaw from rotation matrix R_b^n."""
    pitch = math.asin(np.clip(-R[2, 0], -1.0, 1.0))
    if abs(math.cos(pitch)) > 1e-4:
        roll = math.atan2(R[2, 1], R[2, 2])
        yaw  = math.atan2(R[1, 0], R[0, 0])
    else:
        roll = math.atan2(-R[1, 2], R[1, 1])
        yaw  = 0.0
    return roll, pitch, yaw


class StrapdownINS:
    """
    Classic Strapdown Inertial Navigation System mechanization.
    Tracks 3D position [e, n, u], velocity [ve, vn, vu], and attitude R_b^n.
    """

    def __init__(self, ref_lat_deg: float, ref_lon_deg: float, ref_alt_m: float = 0.0):
        self.ref_lat = ref_lat_deg
        self.ref_lon = ref_lon_deg
        self.ref_alt = ref_alt_m

        # State vectors (in ENU frame)
        self.pos_enu = np.zeros(3, dtype=np.float64)  # [e, n, u] in meters
        self.vel_enu = np.zeros(3, dtype=np.float64)  # [ve, vn, vu] in m/s
        self.R_b_n   = np.eye(3, dtype=np.float64)    # Body to Navigation rotation matrix

        # Gravity vector in ENU [0, 0, -g]
        self.gravity = np.array([0.0, 0.0, -GRAVITY_STANDARD], dtype=np.float64)

        # Previous acceleration in nav frame for trapezoidal integration
        self.last_acc_n: Optional[np.ndarray] = None
        self.last_timestamp: Optional[float] = None

        # Distance traveled accumulator
        self.total_distance_m = 0.0

    def initialize_state(self, lat_deg: float, lon_deg: float, alt_m: float,
                         heading_deg: float = 0.0, speed_mps: float = 0.0,
                         pitch_deg: float = 0.0, roll_deg: float = 0.0):
        """Initialize position, velocity, and orientation."""
        self.pos_enu = geodetic_to_enu(lat_deg, lon_deg, alt_m, self.ref_lat, self.ref_lon, self.ref_alt)
        
        # Heading: 0 deg = North, 90 deg = East in navigation conventions
        # Convert compass heading to ENU yaw (0 = East, 90 = North)
        enu_yaw_rad = math.radians((450.0 - heading_deg) % 360.0)
        self.R_b_n = euler_to_rotation_matrix(math.radians(roll_deg), math.radians(pitch_deg), enu_yaw_rad)

        # Initial velocity along forward body axis (X) projected to ENU
        v_body = np.array([speed_mps, 0.0, 0.0], dtype=np.float64)
        self.vel_enu = self.R_b_n @ v_body
        self.last_acc_n = None
        self.last_timestamp = None

    def update_attitude_from_orientation(self, yaw_deg: float, pitch_deg: float, roll_deg: float):
        """Directly update attitude matrix from fused orientation sensor (e.g. Android Sensor Fusion)."""
        enu_yaw_rad = math.radians((450.0 - yaw_deg) % 360.0)
        self.R_b_n = euler_to_rotation_matrix(
            math.radians(roll_deg),
            math.radians(pitch_deg),
            enu_yaw_rad
        )

    def integrate_gyro(self, gyro_body_rad_s: np.ndarray, dt: float):
        """Update attitude matrix R_b^n by integrating 3-axis gyroscope rates."""
        angle = np.linalg.norm(gyro_body_rad_s) * dt
        if angle < 1e-8:
            return

        axis = gyro_body_rad_s / (np.linalg.norm(gyro_body_rad_s) + 1e-12)
        # Rodrigues' formula for rotation vector update
        skew = np.array([
            [0.0, -axis[2], axis[1]],
            [axis[2], 0.0, -axis[0]],
            [-axis[1], axis[0], 0.0]
        ])
        delta_R = np.eye(3) + math.sin(angle) * skew + (1.0 - math.cos(angle)) * (skew @ skew)
        self.R_b_n = self.R_b_n @ delta_R

        # Orthonormalize rotation matrix to prevent numerical creep
        u, _, vt = np.linalg.svd(self.R_b_n)
        self.R_b_n = u @ vt

    def step(self, accel_body: np.ndarray, dt: float,
             gyro_body: Optional[np.ndarray] = None,
             gravity_body: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """
        Advance SINS by one time step dt:
         1. Optional gyro attitude update
         2. Transform specific force to navigation frame
         3. Remove gravity
         4. Trapezoidal velocity and position integration
        """
        if dt <= 0.0 or dt > 5.0:
            dt = 0.5  # Fallback for unexpected jitter

        # 1. Update orientation if gyro provided
        if gyro_body is not None:
            self.integrate_gyro(gyro_body, dt)

        # 2. Specific force in navigation frame
        if gravity_body is not None:
            # If phone provides isolated linear acceleration (gravity subtracted)
            f_n = self.R_b_n @ (accel_body - gravity_body)
            acc_n = f_n
        else:
            # Standard specific force with gravity compensation in ENU frame
            f_n = self.R_b_n @ accel_body
            acc_n = f_n + self.gravity

        # 3. Trapezoidal integration
        if self.last_acc_n is None:
            self.last_acc_n = acc_n

        avg_acc = 0.5 * (self.last_acc_n + acc_n)
        prev_vel = self.vel_enu.copy()
        self.vel_enu += avg_acc * dt
        self.pos_enu += 0.5 * (prev_vel + self.vel_enu) * dt

        self.last_acc_n = acc_n.copy()

        # Track horizontal distance
        delta_dist = math.sqrt((0.5 * (prev_vel[0] + self.vel_enu[0]) * dt)**2 +
                               (0.5 * (prev_vel[1] + self.vel_enu[1]) * dt)**2)
        self.total_distance_m += delta_dist

        # Compute current geodetic lat/lon
        lat, lon, alt = enu_to_geodetic(
            self.pos_enu[0], self.pos_enu[1], self.pos_enu[2],
            self.ref_lat, self.ref_lon, self.ref_alt
        )

        roll, pitch, yaw = rotation_matrix_to_euler(self.R_b_n)
        compass_heading = (450.0 - math.degrees(yaw)) % 360.0
        speed_mps = float(np.linalg.norm(self.vel_enu[:2]))

        return {
            "pos_enu": self.pos_enu.copy(),
            "vel_enu": self.vel_enu.copy(),
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "speed_mps": speed_mps,
            "speed_kmh": speed_mps * 3.6,
            "heading_deg": compass_heading,
            "roll_deg": math.degrees(roll),
            "pitch_deg": math.degrees(pitch),
            "total_dist_m": self.total_distance_m
        }
