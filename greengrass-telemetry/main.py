"""
Omegga Robot Telemetry Publisher - Greengrass Component
Mimics real Motorcortex robot data and publishes to AWS IoT Core via IPC.
"""

import time
import json
import math
import random
import argparse
import awsiot.greengrasscoreipc
from awsiot.greengrasscoreipc.model import (
    QOS,
    PublishToIoTCoreRequest
)

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
PUBLISH_INTERVAL_S = 1.0
TOPIC_TELEMETRY    = "omegga/robot/{robot_id}/telemetry"
QOS_LEVEL          = QOS.AT_LEAST_ONCE


# ─────────────────────────────────────────────
# Mock Motorcortex Robot State
# ─────────────────────────────────────────────
class MockRobotState:
    """
    Simulates realistic robot data that would come from
    the Motorcortex parameter tree in a real deployment.
    """

    def __init__(self, robot_id: str):
        self.robot_id = robot_id
        self.t = 0.0                    # time counter for realistic variation
        self.eggs_processed = 0
        self.cycle_count = 0
        self.base_cpu_temp = 55.0       # baseline CPU temperature
        self.ethercat_state = "OP"      # EtherCAT state: PRE-OP, SAFE-OP, OP

    def get_joint_positions(self) -> list:
        """6-DOF robot arm joint positions in degrees - sinusoidal motion pattern"""
        return [
            round(45.0  * math.sin(self.t * 0.3) + random.gauss(0, 0.02), 3),
            round(30.0  * math.sin(self.t * 0.2 + 1.0) + random.gauss(0, 0.02), 3),
            round(20.0  * math.cos(self.t * 0.4) + random.gauss(0, 0.02), 3),
            round(15.0  * math.sin(self.t * 0.5 + 0.5) + random.gauss(0, 0.02), 3),
            round(10.0  * math.cos(self.t * 0.6) + random.gauss(0, 0.02), 3),
            round(5.0   * math.sin(self.t * 0.7 + 2.0) + random.gauss(0, 0.02), 3),
        ]

    def get_joint_torques(self) -> list:
        """Joint torques in Nm - correlated with position (like real servo load)"""
        return [
            round(abs(12.0 * math.sin(self.t * 0.3)) + random.gauss(0, 0.1), 3),
            round(abs(8.0  * math.sin(self.t * 0.2)) + random.gauss(0, 0.1), 3),
            round(abs(5.0  * math.cos(self.t * 0.4)) + random.gauss(0, 0.05), 3),
            round(abs(3.0  * math.sin(self.t * 0.5)) + random.gauss(0, 0.05), 3),
            round(abs(2.0  * math.cos(self.t * 0.6)) + random.gauss(0, 0.03), 3),
            round(abs(1.0  * math.sin(self.t * 0.7)) + random.gauss(0, 0.03), 3),
        ]

    def get_joint_velocities(self) -> list:
        """Joint velocities in deg/s"""
        return [
            round(13.5  * math.cos(self.t * 0.3) + random.gauss(0, 0.05), 3),
            round(6.0   * math.cos(self.t * 0.2) + random.gauss(0, 0.05), 3),
            round(-8.0  * math.sin(self.t * 0.4) + random.gauss(0, 0.05), 3),
            round(7.5   * math.cos(self.t * 0.5) + random.gauss(0, 0.03), 3),
            round(-6.0  * math.sin(self.t * 0.6) + random.gauss(0, 0.03), 3),
            round(3.5   * math.cos(self.t * 0.7) + random.gauss(0, 0.02), 3),
        ]

    def get_spectroscopy(self) -> dict:
        """NIR spectroscopy sensor data for egg detection"""
        # Simulate egg detection cycle: every ~3 seconds an egg is detected
        egg_in_progress = (self.cycle_count % 3) == 0
        confidence = round(random.uniform(0.85, 0.99), 3) if egg_in_progress else 0.0
        classification = random.choice(["fertile", "infertile"]) if egg_in_progress else "none"

        return {
            "sensor_status": "OK",
            "egg_detected": egg_in_progress,
            "classification": classification,
            "confidence": confidence,
            "wavelength_nm": 850,
            "intensity": round(random.uniform(0.6, 0.95), 3),
            "calibration_drift_pct": round(random.uniform(0.0, 0.5), 3),
        }

    def get_system_metrics(self) -> dict:
        """System-level metrics: CPU, RAM, temperature"""
        # Simulate gradual CPU temp increase then cooling
        temp_variation = 5.0 * math.sin(self.t * 0.05)
        return {
            "cpu_temp_c": round(self.base_cpu_temp + temp_variation + random.gauss(0, 0.3), 1),
            "cpu_usage_pct": round(random.uniform(25.0, 45.0), 1),
            "ram_used_mb": round(random.uniform(512, 768), 1),
            "ram_total_mb": 2048,
            "disk_used_gb": round(random.uniform(8.0, 9.0), 2),
            "uptime_s": int(self.t),
        }

    def get_ethercat_status(self) -> dict:
        """EtherCAT bus health - critical for real-time control"""
        # Occasionally simulate a minor jitter spike
        jitter = random.gauss(8.0, 1.5)
        if random.random() < 0.01:   # 1% chance of jitter spike
            jitter = random.uniform(50.0, 120.0)

        return {
            "state": self.ethercat_state,
            "slave_count": 6,
            "lost_frames": 0,
            "cycle_jitter_us": round(abs(jitter), 2),
            "cycle_time_ms": round(1.0 + random.gauss(0, 0.002), 4),
        }

    def tick(self) -> dict:
        """Advance simulation by one step and return full telemetry payload"""
        self.t += PUBLISH_INTERVAL_S
        self.cycle_count += 1

        # Increment egg counter periodically
        if self.cycle_count % 3 == 0:
            self.eggs_processed += 1

        return {
            "robot_id":        self.robot_id,
            "timestamp_ms":    int(time.time() * 1000),
            "motorcortex": {
                "state":           "RUNNING",
                "control_mode":    "CSP",               # Cyclic Synchronous Position
                "joint_positions": self.get_joint_positions(),
                "joint_velocities":self.get_joint_velocities(),
                "joint_torques":   self.get_joint_torques(),
            },
            "ethercat":         self.get_ethercat_status(),
            "spectroscopy":     self.get_spectroscopy(),
            "system":           self.get_system_metrics(),
            "production": {
                "eggs_processed":  self.eggs_processed,
                "cycle_count":     self.cycle_count,
                "throughput_per_min": round(self.eggs_processed / max(self.t / 60, 0.01), 1),
            }
        }


# ─────────────────────────────────────────────
# Greengrass IPC Publisher
# ─────────────────────────────────────────────
class RobotTelemetryPublisher:

    def __init__(self, robot_id: str):
        self.robot_id = robot_id
        self.robot = MockRobotState(robot_id)
        self.ipc_client = awsiot.greengrasscoreipc.connect()

    def publish(self, topic: str, payload: dict):
        request = PublishToIoTCoreRequest(
            topic_name=topic,
            qos=QOS_LEVEL,
            payload=json.dumps(payload).encode("utf-8"),
        )
        operation = self.ipc_client.new_publish_to_iot_core()
        operation.activate(request)
        result = operation.get_response()
        result.result(timeout=5.0)

    def run(self):
        topic = TOPIC_TELEMETRY.format(robot_id=self.robot_id)
        print(f"[{self.robot_id}] Starting telemetry publisher → {topic}")

        while True:
            try:
                payload = self.robot.tick()
                self.publish(topic, payload)
                print(f"[{self.robot_id}] Published | eggs={payload['production']['eggs_processed']} "
                      f"| cpu={payload['system']['cpu_temp_c']}°C "
                      f"| ethercat={payload['ethercat']['state']}")
            except Exception as e:
                print(f"[{self.robot_id}] Publish error: {e}")

            time.sleep(PUBLISH_INTERVAL_S)


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot-id", default="robot-001", help="Unique robot identifier")
    parser.add_argument("--mock", action="store_true", help="Print to stdout instead of IPC (for local testing)")
    args = parser.parse_args()

    if args.mock:
        # Local testing without Greengrass IPC
        robot = MockRobotState(args.robot_id)
        print(f"[MOCK MODE] Publishing robot data for {args.robot_id}")
        while True:
            payload = robot.tick()
            print(json.dumps(payload, indent=2))
            time.sleep(PUBLISH_INTERVAL_S)
    else:
        publisher = RobotTelemetryPublisher(args.robot_id)
        publisher.run()


if __name__ == "__main__":
    main()
