package org.sih2026.idr.sensors

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.SystemClock

data class RawSensorSample(
    val timestampMs: Long,
    val accelX: Float,
    val accelY: Float,
    val accelZ: Float,
    val gyroX: Float,
    val gyroY: Float,
    val gyroZ: Float,
    val yawDeg: Float,
    val pitchDeg: Float,
    val rollDeg: Float,
    val gnssLat: Double?,
    val gnssLon: Double?,
    val gnssSpeedKmh: Float?,
    val gnssAccuracyM: Float?
)

class SensorCollector(
    private val context: Context,
    private val onSampleReady: (RawSensorSample) -> Unit
) : SensorEventListener, LocationListener {

    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val locationManager = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager

    // Cached values
    private var lastAccel = FloatArray(3)
    private var lastGyro = FloatArray(3)
    private var lastOrientation = FloatArray(3) // [yaw, pitch, roll]
    private var lastLocation: Location? = null

    private var isCollecting = false

    fun start(sampleRateUs: Int = 500_000) { // 2 Hz by default matching IO-VNBD dataset rate
        if (isCollecting) return

        sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let {
            sensorManager.registerListener(this, it, sampleRateUs)
        }
        sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let {
            sensorManager.registerListener(this, it, sampleRateUs)
        }
        sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.let {
            sensorManager.registerListener(this, it, sampleRateUs)
        }

        try {
            locationManager.requestLocationUpdates(
                LocationManager.GPS_PROVIDER,
                1000L, // 1 Hz GNSS
                0f,
                this
            )
        } catch (e: SecurityException) {
            // Handled in MainActivity permission checks
        }

        isCollecting = true
    }

    fun stop() {
        if (!isCollecting) return
        sensorManager.unregisterListener(this)
        locationManager.removeUpdates(this)
        isCollecting = false
    }

    override fun onSensorChanged(event: SensorEvent?) {
        if (event == null) return

        when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                System.arraycopy(event.values, 0, lastAccel, 0, 3)
                dispatchSample()
            }
            Sensor.TYPE_GYROSCOPE -> {
                System.arraycopy(event.values, 0, lastGyro, 0, 3)
            }
            Sensor.TYPE_ROTATION_VECTOR -> {
                val rotationMatrix = FloatArray(9)
                SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values)
                val orientation = FloatArray(3)
                SensorManager.getOrientation(rotationMatrix, orientation)
                // Convert azimuth/pitch/roll radians to degrees
                lastOrientation[0] = Math.toDegrees(orientation[0].toDouble()).toFloat()
                lastOrientation[1] = Math.toDegrees(orientation[1].toDouble()).toFloat()
                lastOrientation[2] = Math.toDegrees(orientation[2].toDouble()).toFloat()
            }
        }
    }

    private fun dispatchSample() {
        val nowMs = SystemClock.elapsedRealtime()
        val loc = lastLocation

        val sample = RawSensorSample(
            timestampMs = nowMs,
            accelX = lastAccel[0],
            accelY = lastAccel[1],
            accelZ = lastAccel[2],
            gyroX = lastGyro[0],
            gyroY = lastGyro[1],
            gyroZ = lastGyro[2],
            yawDeg = lastOrientation[0],
            pitchDeg = lastOrientation[1],
            rollDeg = lastOrientation[2],
            gnssLat = loc?.latitude,
            gnssLon = loc?.longitude,
            gnssSpeedKmh = loc?.let { it.speed * 3.6f },
            gnssAccuracyM = loc?.accuracy
        )
        onSampleReady(sample)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    override fun onLocationChanged(location: Location) {
        lastLocation = location
    }
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
    override fun onProviderEnabled(provider: String) {}
    override fun onProviderDisabled(provider: String) {}
}
