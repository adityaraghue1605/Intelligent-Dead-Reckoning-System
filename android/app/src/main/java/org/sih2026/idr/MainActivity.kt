package org.sih2026.idr

import android.Manifest
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import org.sih2026.idr.engine.AndroidNavState
import org.sih2026.idr.engine.IDRLocalClient
import org.sih2026.idr.sensors.RawSensorSample
import org.sih2026.idr.sensors.SensorCollector

class MainActivity : AppCompatActivity() {

    private lateinit var sensorCollector: SensorCollector
    private lateinit var idrClient: IDRLocalClient
    private lateinit var prefs: SharedPreferences

    // UI containers
    private lateinit var webView: WebView
    private lateinit var layoutNativeHud: LinearLayout
    private lateinit var btnTabWeb: Button
    private lateinit var btnTabHud: Button
    private lateinit var btnSettingsServer: Button

    // Native HUD UI elements
    private lateinit var tvNavMode: TextView
    private lateinit var tvSpeed: TextView
    private lateinit var tvHeading: TextView
    private lateinit var tvCoordinates: TextView
    private lateinit var tvDriftPct: TextView
    private lateinit var tvDriftMeters: TextView
    private lateinit var btnToggleBlackout: Button

    private var isBlackoutActive = false
    private var serverUrl = "http://10.12.61.144:8000"
    private var isUsingLocalAssets = true

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        supportActionBar?.hide()
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences("idr_prefs", Context.MODE_PRIVATE)
        serverUrl = prefs.getString("server_url", "http://10.12.61.144:8000") ?: "http://10.12.61.144:8000"

        // Initialize UI References
        webView = findViewById(R.id.webView)
        layoutNativeHud = findViewById(R.id.layout_native_hud)
        btnTabWeb = findViewById(R.id.btn_tab_web)
        btnTabHud = findViewById(R.id.btn_tab_hud)
        btnSettingsServer = findViewById(R.id.btn_settings_server)

        tvNavMode = findViewById(R.id.tv_nav_mode)
        tvSpeed = findViewById(R.id.tv_speed_value)
        tvHeading = findViewById(R.id.tv_heading_value)
        tvCoordinates = findViewById(R.id.tv_coordinates)
        tvDriftPct = findViewById(R.id.tv_drift_pct)
        tvDriftMeters = findViewById(R.id.tv_drift_meters)
        btnToggleBlackout = findViewById(R.id.btn_simulate_tunnel)

        setupWebView()
        setupTabSwitching()

        // Initialize IDR Local Client in background
        val wsUrl = serverUrl.replace("http://", "ws://").replace("https://", "wss://") + "/ws/telemetry"
        idrClient = IDRLocalClient(wsUrl) { state ->
            runOnUiThread { updateUI(state) }
        }

        // Initialize Hardware Sensor Collector
        sensorCollector = SensorCollector(this) { sample ->
            idrClient.processSampleLocally(sample)
            dispatchSensorToWeb(sample)
        }

        btnToggleBlackout.setOnClickListener {
            toggleBlackout(!isBlackoutActive)
        }

        btnSettingsServer.setOnClickListener {
            showServerSettingsDialog()
        }

        checkPermissionsAndStart()
    }

    private fun setupWebView() {
        webView.setBackgroundColor(Color.parseColor("#0A0D14"))
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            allowFileAccess = true
            allowContentAccess = true
            allowFileAccessFromFileURLs = true
            allowUniversalAccessFromFileURLs = true
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = false
            displayZoomControls = false
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(consoleMessage: android.webkit.ConsoleMessage?): Boolean {
                android.util.Log.d("IDR_CONSOLE", "${consoleMessage?.message()} (Line ${consoleMessage?.lineNumber()})")
                return true
            }
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
                super.onReceivedError(view, request, error)
                if (request?.isForMainFrame == true && !isUsingLocalAssets) {
                    runOnUiThread {
                        loadBundledAssets()
                    }
                }
            }

            @Deprecated("Deprecated in Java")
            override fun onReceivedError(view: WebView?, errorCode: Int, description: String?, failingUrl: String?) {
                super.onReceivedError(view, errorCode, description, failingUrl)
                if (!isUsingLocalAssets) {
                    runOnUiThread {
                        loadBundledAssets()
                    }
                }
            }
        }

        webView.addJavascriptInterface(WebAppBridge(), "AndroidBridge")
        loadWebDashboard()
    }

    private fun loadWebDashboard() {
        loadBundledAssets()
    }

    private fun loadBundledAssets() {
        isUsingLocalAssets = true
        webView.loadUrl("file:///android_asset/www/index.html")
    }

    private fun setupTabSwitching() {
        btnTabWeb.setOnClickListener {
            switchViewMode(showWeb = true)
        }
        btnTabHud.setOnClickListener {
            switchViewMode(showWeb = false)
        }
    }

    private fun switchViewMode(showWeb: Boolean) {
        if (showWeb) {
            webView.visibility = View.VISIBLE
            layoutNativeHud.visibility = View.GONE
            btnTabWeb.setTextColor(Color.parseColor("#00F2FE"))
            btnTabWeb.setBackgroundColor(Color.parseColor("#0F172A"))
            btnTabHud.setTextColor(Color.parseColor("#94A3B8"))
            btnTabHud.setBackgroundColor(Color.parseColor("#1E293B"))
        } else {
            webView.visibility = View.GONE
            layoutNativeHud.visibility = View.VISIBLE
            btnTabWeb.setTextColor(Color.parseColor("#94A3B8"))
            btnTabWeb.setBackgroundColor(Color.parseColor("#1E293B"))
            btnTabHud.setTextColor(Color.parseColor("#00F2FE"))
            btnTabHud.setBackgroundColor(Color.parseColor("#0F172A"))
        }
    }

    private fun connectToServer(targetUrl: String) {
        serverUrl = targetUrl
        prefs.edit().putString("server_url", serverUrl).apply()

        idrClient.disconnect()
        val ws = serverUrl.replace("http://", "ws://").replace("https://", "wss://") + "/ws/telemetry"
        idrClient = IDRLocalClient(ws) { state ->
            runOnUiThread { updateUI(state) }
        }
        idrClient.connect()

        runOnUiThread {
            webView.evaluateJavascript("if(typeof reconnectToServer==='function'){reconnectToServer('$serverUrl');}", null)
        }
        Toast.makeText(this, "Connecting to $serverUrl", Toast.LENGTH_SHORT).show()
    }

    private fun showServerSettingsDialog() {
        val options = arrayOf(
            "📶 Live Wi-Fi Server (http://10.12.61.144:8000)",
            "⚡ Autonomous Mode (No Server Needed)",
            "💻 Android Emulator (http://10.0.2.2:8000)",
            "✏️ Enter Custom IP / Host"
        )

        AlertDialog.Builder(this)
            .setTitle("⚙️ Connection Setup")
            .setItems(options) { _, which ->
                when (which) {
                    0 -> connectToServer("http://10.12.61.144:8000")
                    1 -> {
                        isUsingLocalAssets = true
                        serverUrl = "file:///android_asset/www/index.html"
                        prefs.edit().putString("server_url", serverUrl).apply()
                        idrClient.disconnect()
                        runOnUiThread {
                            webView.evaluateJavascript("if(typeof startAutonomousOnDeviceMode==='function'){startAutonomousOnDeviceMode();}", null)
                        }
                        Toast.makeText(this, "⚡ Autonomous On-Device Mode Active", Toast.LENGTH_SHORT).show()
                    }
                    2 -> connectToServer("http://10.0.2.2:8000")
                    3 -> showCustomIpInputDialog()
                }
            }
            .setNegativeButton("Close", null)
            .show()
    }

    private fun showCustomIpInputDialog() {
        val input = EditText(this).apply {
            setText(serverUrl)
            hint = "e.g. http://10.12.61.144:8000"
            setPadding(40, 30, 40, 30)
        }

        AlertDialog.Builder(this)
            .setTitle("✏️ Custom Server IP")
            .setMessage("Enter the FastAPI Server URL:")
            .setView(input)
            .setPositiveButton("Connect") { _, _ ->
                var entered = input.text.toString().trim()
                if (entered.isNotEmpty()) {
                    if (!entered.startsWith("http://") && !entered.startsWith("https://")) {
                        entered = "http://$entered"
                    }
                    connectToServer(entered)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun dispatchSensorToWeb(sample: RawSensorSample) {
        try {
            val json = JSONObject().apply {
                put("timestampMs", sample.timestampMs)
                put("accelX", sample.accelX)
                put("accelY", sample.accelY)
                put("accelZ", sample.accelZ)
                put("gyroX", sample.gyroX)
                put("gyroY", sample.gyroY)
                put("gyroZ", sample.gyroZ)
                put("yawDeg", sample.yawDeg)
                put("pitchDeg", sample.pitchDeg)
                put("rollDeg", sample.rollDeg)
            }
            runOnUiThread {
                webView.evaluateJavascript("if(window.onAndroidSensorData){window.onAndroidSensorData($json);}", null)
            }
        } catch (e: Exception) {
            // Ignore during rapid sensor streaming
        }
    }

    private fun toggleBlackout(active: Boolean) {
        isBlackoutActive = active
        idrClient.setBlackoutSimulation(isBlackoutActive)
        btnToggleBlackout.text = if (isBlackoutActive) "Restore GNSS" else "Simulate Tunnel Blackout"
        btnToggleBlackout.setBackgroundColor(if (isBlackoutActive) Color.parseColor("#10B981") else Color.parseColor("#EF4444"))
        
        // Notify web view
        runOnUiThread {
            webView.evaluateJavascript("if(typeof setGpsSignal==='function'){setGpsSignal(${!active});}", null)
        }
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
        webView.destroy()
    }

    // JavaScript Bridge Exposed to Web Dashboard
    inner class WebAppBridge {
        @JavascriptInterface
        fun getBackendUrl(): String = serverUrl

        @JavascriptInterface
        fun isNativeApp(): Boolean = true

        @JavascriptInterface
        fun vibrate(milliseconds: Long) {
            val vibrator = getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator?.vibrate(VibrationEffect.createOneShot(milliseconds, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                vibrator?.vibrate(milliseconds)
            }
        }

        @JavascriptInterface
        fun toggleBlackout(active: Boolean) {
            runOnUiThread {
                this@MainActivity.toggleBlackout(active)
            }
        }
    }
}
