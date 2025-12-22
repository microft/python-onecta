import logging
import sqlite3
import os
import time
import queue
import threading
import paho.mqtt.client as mqtt
from datetime import datetime

# ---------------- CONFIG ----------------
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "#"          # subscribe to ALL topics
MQTT_QOS = 0
USERNAME = os.environ.get("MQTT_USERNAME")       # set if you enabled authentication
PASSWORD = os.environ.get("MQTT_PASSWD")   # set if you enabled authentication

DB_PATH = "mqtt_events.db"
BATCH_SIZE = 100          # insert rows in batches
FLUSH_INTERVAL = 1.0      # seconds
# ----------------------------------------


_logger = logging.getLogger(__name__)



# Thread-safe queue for decoupling MQTT from SQLite
msg_queue = queue.Queue(maxsize=10000)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Performance-oriented pragmas
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA temp_store=MEMORY;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mqtt_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            payload BLOB NOT NULL,
            qos INTEGER,
            retain INTEGER,
            ts INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def db_worker():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()

    batch = []
    last_flush = time.time()

    while True:
        try:
            item = msg_queue.get(timeout=0.5)
            batch.append(item)
        except queue.Empty:
            pass

        now = time.time()
        if len(batch) >= BATCH_SIZE or (batch and now - last_flush >= FLUSH_INTERVAL):
            cur.executemany(
                "INSERT INTO mqtt_messages (topic, payload, qos, retain, ts) VALUES (?, ?, ?, ?, ?)",
                batch
            )
            conn.commit()
            batch.clear()
            last_flush = now
            print(f"{datetime.now().isoformat()} sqlite flush")


def on_connect(client, userdata, flags, rc):
    print("Connected to MQTT broker:", rc)
    client.subscribe(MQTT_TOPIC, qos=MQTT_QOS)


def on_message(client, userdata, msg):
    try:
        msg_queue.put_nowait((
            msg.topic,
            msg.payload,
            msg.qos,
            int(msg.retain),
            int(time.time())
        ))
        _logger.info(f"Writting {msg.payload}")
    except queue.Full:
        # Drop messages if DB can't keep up
        pass


def main():
    init_db()

    # Start DB writer thread
    t = threading.Thread(target=db_worker, daemon=True)
    t.start()

    client = mqtt.Client()
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
