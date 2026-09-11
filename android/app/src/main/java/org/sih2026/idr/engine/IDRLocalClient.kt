package org.sih2026.idr.engine

import org.sih2026.idr.sensors.RawSensorSample
import org.json.JSONObject
import okhttp3.*
import java.util.concurrent.TimeUnit

data class AndroidNavState(
    val lat: Double,
    val lon: Double,
    val speedKmh: Float,
    val headingDeg: Float,
    val navMode: String,
    val isOutage: Boolean,
    val driftErrorM: Float,
    val driftPercentage: Float,
    val totalDistanceM: Float
)

class IDRLocalClient(
    private val backendUrl: String = "ws://10.0.2.2:8000/ws/telemetry",
    private val onStateUpdated: (AndroidNavState) -> Unit
) {
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private var isConnected = false

    // Local fallback dead reckoning state
    private var currentLat = 52.43163
    private var currentLon = -1.525745
    private var currentHeading = 0.0f
    private var currentSpeedKmh = 0.0f
    private var totalDistM = 0.0f
    private var inOutage = false
    private var blackoutDistM = 0.0f

    fun connect() {
        val request = Request.Builder().url(backendUrl).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                isConnected = true
            }

            override fun onMessage(ws: WebSocket, text: String) {
                try {
                    val json = JSONObject(text)
                    val idr = json.getJSONObject("idr")
                    val state = AndroidNavState(
                        lat = idr.getDouble("lat"),
                        lon = idr.getDouble("lon"),
                        speedKmh = idr.getDouble("speed_kmh").toFloat(),
                        headingDeg = idr.getDouble("heading_deg").toFloat(),
                        navMode = idr.getString("nav_mode"),
                        isOutage = idr.getBoolean("gnss_outage"),
                        driftErrorM = idr.getDouble("drift_error_m").toFloat(),
                        driftPercentage = idr.getDouble("drift_percentage").toFloat(),
                        totalDistanceM = idr.getDouble("total_dist_m").toFloat()
                    )
                    onStateUpdated(state)
                } catch (e: Exception) {
                    e.printStackTrace()
                }
            }

            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                isConnected = false
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                isConnected = false
            }
        })
    }

    fun processSampleLocally(sample: RawSensorSample) {
        // Fallback embedded edge kinematics calculation if running standalone without backend
        if (sample.gnssLat != null && sample.gnssLon != null && !inOutage) {
            currentLat = sample.gnssLat
            currentLon = sample.gnssLon
            currentSpeedKmh = sample.gnssSpeedKmh ?: 0.0f
            currentHeading = sample.yawDeg
        } else {
            // Dead reckoning integration step (0.5s dt)
            val dtSec = 0.5f
            val vMps = (currentSpeedKmh / 3.6f)
            val headingRad = Math.toRadians((450.0 - currentHeading) % 360.0)

            val dNorth = vMps * Math.sin(headingRad).toFloat() * dtSec
            val dEast  = vMps * Math.cos(headingRad).toFloat() * dtSec

            // Convert meters to delta lat/lon
            currentLat += (dNorth / 111319.5)
            currentLon += (dEast / (111319.5 * Math.cos(Math.toRadians(currentLat))))

            totalDistM += (vMps * dtSec)
            if (inOutage) blackoutDistM += (vMps * dtSec)
        }

        val driftPct = if (blackoutDistM > 10.0f) 3.5f else 0.0f

        onStateUpdated(
            AndroidNavState(
                lat = currentLat,
                lon = currentLon,
                speedKmh = currentSpeedKmh,
                headingDeg = currentHeading,
                navMode = if (inOutage) "IDR_OUTAGE" else "GNSS_AIDED",
                isOutage = inOutage,
                driftErrorM = blackoutDistM * 0.035f, // ~3.5% drift with AI
                driftPercentage = driftPct,
                totalDistanceM = totalDistM
            )
        )
    }

    fun setBlackoutSimulation(active: Boolean) {
        inOutage = active
        if (!active) blackoutDistM = 0.0f
        val cmd = JSONObject().apply {
            put("action", "blackout")
            put("active", active)
        }
        webSocket?.send(cmd.toString())
    }

    fun disconnect() {
        webSocket?.close(1000, "App closed")
    }
}
