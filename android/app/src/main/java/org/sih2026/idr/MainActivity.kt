package org.sih2026.idr

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.sih2026.idr.engine.AndroidNavState
import org.sih2026.idr.engine.IDRLocalClient
import org.sih2026.idr.sensors.SensorCollector

class MainActivity : AppCompatActivity() {

    private lateinit var sensorCollector: SensorCollector
    private lateinit var idrClient: IDRLocalClient

    // UI elements
    private lateinit var tvNavMode: TextView
    private lateinit var tvSpeed: TextView
    private lateinit var tvHeading: TextView
    private lateinit var tvCoordinates: TextView
    private lateinit var tvDriftPct: TextView
    private lateinit var tvDriftMeters: TextView
    private lateinit var btnToggleBlackout: Button

    private var isBlackoutActive = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvNavMode = findViewById(R.id.tv_nav_mode)
        tvSpeed = findViewById(R.id.tv_speed_value)
        tvHeading = findViewById(R.id.tv_heading_value)
        tvCoordinates = findViewById(R.id.tv_coordinates)
        tvDriftPct = findViewById(R.id.tv_drift_pct)
        tvDriftMeters = findViewById(R.id.tv_drift_meters)
        btnToggleBlackout = findViewById(R.id.btn_simulate_tunnel)

        // Initialize IDR Client
        idrClient = IDRLocalClient { state ->
            runOnUiThread { updateUI(state) }
        }

        // Initialize Hardware Sensor Collector
        sensorCollector = SensorCollector(this) { sample ->
            idrClient.processSampleLocally(sample)
        }

        btnToggleBlackout.setOnClickListener {
            isBlackoutActive = !isBlackoutActive
            idrClient.setBlackoutSimulation(isBlackoutActive)
            btnToggleBlackout.text = if (isBlackoutActive) "Restore GNSS" else "Simulate Tunnel Blackout"
            btnToggleBlackout.setBackgroundColor(if (isBlackoutActive) Color.parseColor("#10B981") else Color.parseColor("#EF4444"))
        }

        checkPermissionsAndStart()
    }

    private fun checkPermissionsAndStart() {
        val permissions = arrayOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION
        )
        val missing = permissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), 101)
        } else {
            startNavigation()
        }
    }

    private fun startNavigation() {
        sensorCollector.start()
        idrClient.connect()
        Toast.makeText(this, "IDR Navigation Engine Active", Toast.LENGTH_SHORT).show()
    }

    private fun updateUI(state: AndroidNavState) {
        tvSpeed.text = String.format("%.1f", state.speedKmh)
        tvHeading.text = String.format("%03.0f°", state.headingDeg)
        tvCoordinates.text = String.format("%.6f, %.6f", state.lat, state.lon)
        tvDriftPct.text = String.format("%.1f%%", state.driftPercentage)
        tvDriftMeters.text = String.format("%.2f m drift", state.driftErrorM)

        if (state.isOutage) {
            tvNavMode.text = "IDR OUTAGE (TUNNEL)"
            tvNavMode.setTextColor(Color.parseColor("#EF4444"))
        } else {
            tvNavMode.text = "GNSS + INS AIDED"
            tvNavMode.setTextColor(Color.parseColor("#10B981"))
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        sensorCollector.stop()
        idrClient.disconnect()
    }
}
