"""
Omegga Robot Telemetry → InfluxDB Writer
Subscribes to MQTT topics from AWS IoT Core and writes structured
time-series data to InfluxDB for Grafana visualization.

Run: python3 subscriber.py
"""

import json
import time
import os
import ssl
import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from datetime import datetime, timezone

# ─────────────────────────────────────────────
# Configuration — set via environment variables
# ─────────────────────────────────────────────
MQTT_HOST       = os.getenv("MQTT_HOST", "your-iot-endpoint.iot.eu-central-1.amazonaws.com")
MQTT_PORT       = int(os.getenv("MQTT_PORT", "8883"))
MQTT_TOPIC      = os.getenv("MQTT_TOPIC", "omegga/robot/+/telemetry")
MQTT_CERT       = os.getenv("MQTT_CERT", "/certs/certificate.pem")
MQTT_KEY        = os.getenv("MQTT_KEY",  "/certs/private.key")
MQTT_CA         = os.getenv("MQTT_CA",   "/certs/AmazonRootCA1.pem")
MQTT_CLIENT_ID  = os.getenv("MQTT_CLIENT_ID", "omegga-influx-subscriber")

INFLUX_URL      = os.getenv("INFLUX_URL",    "http://localhost:8086")
INFLUX_TOKEN    = os.getenv("INFLUX_TOKEN",  "your-influxdb-token")
INFLUX_ORG      = os.getenv("INFLUX_ORG",   "omegga")
INFLUX_BUCKET   = os.getenv("INFLUX_BUCKET", "robot-telemetry")


# ─────────────────────────────────────────────
# InfluxDB Writer
# ─────────────────────────────────────────────
class InfluxWriter:

    def __init__(self):
        self.client = InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        print(f"[InfluxDB] Connected to {INFLUX_URL} | org={INFLUX_ORG} | bucket={INFLUX_BUCKET}")

    def write(self, payload: dict):
        robot_id  = payload.get("robot_id", "unknown")
        timestamp = payload.get("timestamp_ms", int(time.time() * 1000))
        ts        = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        points    = []

        # ── Joint States (positions, velocities, torques) ──
        mcx = payload.get("motorcortex", {})
        for i, (pos, vel, torq) in enumerate(zip(
            mcx.get("joint_positions",  []),
            mcx.get("joint_velocities", []),
            mcx.get("joint_torques",    []),
        )):
            p = (
                Point("joint_state")
                .tag("robot_id", robot_id)
                .tag("joint", f"J{i+1}")
                .tag("control_mode", mcx.get("control_mode", "CSP"))
                .field("position_deg", pos)
                .field("velocity_dps", vel)
                .field("torque_nm",    torq)
                .time(ts, WritePrecision.MS)
            )
            points.append(p)

        # ── EtherCAT Bus Health ──
        etc = payload.get("ethercat", {})
        points.append(
            Point("ethercat")
            .tag("robot_id",  robot_id)
            .tag("state",     etc.get("state", "UNKNOWN"))
            .field("slave_count",     etc.get("slave_count", 0))
            .field("lost_frames",     etc.get("lost_frames", 0))
            .field("cycle_jitter_us", etc.get("cycle_jitter_us", 0.0))
            .field("cycle_time_ms",   etc.get("cycle_time_ms", 0.0))
            .time(ts, WritePrecision.MS)
        )

        # ── Spectroscopy / Egg Detection ──
        spec = payload.get("spectroscopy", {})
        points.append(
            Point("spectroscopy")
            .tag("robot_id",       robot_id)
            .tag("classification", spec.get("classification", "none"))
            .tag("sensor_status",  spec.get("sensor_status", "UNKNOWN"))
            .field("egg_detected",        int(spec.get("egg_detected", False)))
            .field("confidence",          spec.get("confidence", 0.0))
            .field("intensity",           spec.get("intensity", 0.0))
            .field("calibration_drift_pct", spec.get("calibration_drift_pct", 0.0))
            .time(ts, WritePrecision.MS)
        )

        # ── System Metrics ──
        sys = payload.get("system", {})
        points.append(
            Point("system_metrics")
            .tag("robot_id", robot_id)
            .field("cpu_temp_c",    sys.get("cpu_temp_c", 0.0))
            .field("cpu_usage_pct", sys.get("cpu_usage_pct", 0.0))
            .field("ram_used_mb",   sys.get("ram_used_mb", 0.0))
            .field("ram_total_mb",  sys.get("ram_total_mb", 0.0))
            .field("disk_used_gb",  sys.get("disk_used_gb", 0.0))
            .field("uptime_s",      sys.get("uptime_s", 0))
            .time(ts, WritePrecision.MS)
        )

        # ── Production / Egg Counting ──
        prod = payload.get("production", {})
        points.append(
            Point("production")
            .tag("robot_id", robot_id)
            .field("eggs_processed",    prod.get("eggs_processed", 0))
            .field("cycle_count",       prod.get("cycle_count", 0))
            .field("throughput_per_min",prod.get("throughput_per_min", 0.0))
            .time(ts, WritePrecision.MS)
        )

        self.write_api.write(bucket=INFLUX_BUCKET, record=points)
        print(f"[InfluxDB] Written {len(points)} points | robot={robot_id} | "
              f"eggs={prod.get('eggs_processed',0)} | "
              f"cpu={sys.get('cpu_temp_c',0)}°C")

    def close(self):
        self.client.close()


# ─────────────────────────────────────────────
# MQTT Subscriber
# ─────────────────────────────────────────────
class MqttSubscriber:

    def __init__(self, influx: InfluxWriter):
        self.influx = influx
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)

        # TLS with X.509 certificates (AWS IoT Core)
        self.client.tls_set(
            ca_certs=MQTT_CA,
            certfile=MQTT_CERT,
            keyfile=MQTT_KEY,
            tls_version=ssl.PROTOCOL_TLSv1_2
        )

        self.client.on_connect    = self._on_connect
        self.client.on_message    = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
            client.subscribe(MQTT_TOPIC, qos=1)
            print(f"[MQTT] Subscribed to: {MQTT_TOPIC}")
        else:
            print(f"[MQTT] Connection failed, rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            self.influx.write(payload)
        except json.JSONDecodeError as e:
            print(f"[MQTT] Failed to parse message: {e}")
        except Exception as e:
            print(f"[MQTT] Error writing to InfluxDB: {e}")

    def _on_disconnect(self, client, userdata, rc):
        print(f"[MQTT] Disconnected rc={rc}, reconnecting...")

    def run(self):
        print(f"[MQTT] Connecting to {MQTT_HOST}:{MQTT_PORT}")
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_forever()


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    influx = InfluxWriter()
    try:
        subscriber = MqttSubscriber(influx)
        subscriber.run()
    except KeyboardInterrupt:
        print("\n[Subscriber] Shutting down...")
    finally:
        influx.close()
