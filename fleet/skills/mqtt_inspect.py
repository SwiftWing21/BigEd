"""
MQTT topic inspector — connect to broker, subscribe, capture messages.

Requires: MQTT_HOST in ~/.secrets (default: localhost)
Optional: MQTT_PORT (default: 1883), MQTT_USER, MQTT_PASS

Actions:
  listen   — subscribe to topic(s) for N seconds, return captured messages
  publish  — publish a message to a topic

Payload:
  topics       list   ["#"] (default) or ["home/+/temperature", "zigbee2mqtt/#"]
  duration_sec int    10 (default) — how long to listen
  topic        str    (for publish) target topic
  message      str    (for publish) payload string

Returns: {action, messages: [...], count, duration_sec}
"""
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
MQTT_DIR = KNOWLEDGE_DIR / "mqtt"
REQUIRES_NETWORK = True


def run(payload, config):
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    user = os.environ.get("MQTT_USER", "")
    password = os.environ.get("MQTT_PASS", "")
    action = payload.get("action", "listen")

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return {"error": "paho-mqtt not installed. Run: pip install paho-mqtt"}

    if action == "listen":
        topics = payload.get("topics", ["#"])
        # Block dangerous wildcard subscriptions
        for topic in topics:
            if topic == "#" or topic == "$SYS/#":
                return json.dumps({"error": "Wildcard topic '#' blocked for security. Specify explicit topics."})
        duration = min(payload.get("duration_sec", 10), 60)  # cap at 60s
        messages = []
        connected = threading.Event()

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                for t in topics:
                    client.subscribe(t)
                connected.set()

        def on_message(client, userdata, msg):
            try:
                payload_str = msg.payload.decode("utf-8", errors="replace")
                # Try to parse as JSON
                try:
                    payload_data = json.loads(payload_str)
                except (json.JSONDecodeError, ValueError):
                    payload_data = payload_str
                messages.append({
                    "topic": msg.topic,
                    "payload": payload_data,
                    "qos": msg.qos,
                    "retain": msg.retain,
                    "ts": datetime.now().isoformat(),
                })
            except Exception:
                pass

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if user:
            client.username_pw_set(user, password)
        client.on_connect = on_connect
        client.on_message = on_message

        try:
            client.connect(host, port, keepalive=duration + 10)
            client.loop_start()
            if not connected.wait(timeout=5):
                client.loop_stop()
                return {"error": f"MQTT connection timeout to {host}:{port}"}
            time.sleep(duration)
            client.loop_stop()
            client.disconnect()
        except Exception as e:
            return {"error": f"MQTT connection failed: {e}", "host": host, "port": port}

        # Save capture
        MQTT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = MQTT_DIR / f"mqtt_capture_{ts}.json"
        out_file.write_text(json.dumps(messages, indent=2))

        # Summarize by topic
        topic_counts = {}
        for m in messages:
            topic_counts[m["topic"]] = topic_counts.get(m["topic"], 0) + 1

        return {
            "action": "listen",
            "count": len(messages),
            "duration_sec": duration,
            "topics_seen": topic_counts,
            "messages": messages[:100],  # cap output
            "saved_to": str(out_file),
        }

    elif action == "publish":
        topic = payload.get("topic", "")
        message = payload.get("message", "")
        if not topic:
            return {"error": "topic required for publish"}

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if user:
            client.username_pw_set(user, password)
        try:
            client.connect(host, port, keepalive=10)
            result = client.publish(topic, message, qos=1)
            result.wait_for_publish(timeout=5)
            client.disconnect()
            return {"action": "publish", "topic": topic, "status": "published"}
        except Exception as e:
            return {"error": f"MQTT publish failed: {e}"}

    else:
        return {"error": f"Unknown action: {action}"}
