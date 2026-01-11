#!/usr/bin/env python3

"""A simple script to print some sensor temperatures every 10 minutes."""

import os
from datetime import datetime
import time
import logging
import gzip
import json
import paho.mqtt.client as mqtt

from daikin import Daikin

_logger = logging.getLogger(__name__)


# ---------------------------
# MQTT broker configuration
# ---------------------------
BROKER = "192.168.1.10"        # or IP address of your Mosquitto broker
PORT = 1883                 # default MQTT port
USERNAME = os.environ.get("MQTT_USERNAME")       # set if you enabled authentication
PASSWORD = os.environ.get("MQTT_PASSWD")   # set if you enabled authentication
TOPIC_TEMPLATE = "sensors/temperature/{device_id}"

TIMEOUT = 60*15



def setup_logging(zf):
    # log to both zf and console

    gz_log_handler = logging.StreamHandler(zf)
    _logger.addHandler(gz_log_handler)

    stderr_log_handler = logging.StreamHandler()
    _logger.addHandler(stderr_log_handler)

    # prefix timestamp onto the file logger
    formatter = logging.Formatter(
        fmt="%(asctime)s: %(message)s", datefmt="%Y-%m-%d--%H:%M"
    )
    gz_log_handler.setFormatter(formatter)

    _logger.setLevel(logging.DEBUG)


def monitor():
    daikin = Daikin()

    while True:
        xpto = daikin.get_all_management_points()

        print(datetime.now().isoformat())
        for mp in xpto:

            ip = mp["gateway"]["ipAddress"]["value"]
            device_id = ip
            name = mp["climateControl"]["name"]["value"]
            mode = mp["climateControl"]["operationMode"]["value"]
            on_off = mp["climateControl"]["onOffMode"]["value"]
            mz = mp["climateControl"]["sensoryData"]["value"]
            # lwt = mz["leavingWaterTemperature"]["value"]
            outdoor = mz["outdoorTemperature"]["value"]
            room_temp = mz["roomTemperature"]["value"]


            #tc = mp["climateControl"]["temperatureControl"]["value"]
            # should this be "auto", or "heating" ?
            # target = tc["operationModes"]["auto"]["setpoints"]["roomTemperature"]["value"]
            #offs = tc["operationModes"]["auto"]["setpoints"]["leavingWaterOffset"]["value"]

            #hwt = mp["domesticHotWaterTank"]["sensoryData"]["value"]
            #hw = hwt["tankTemperature"]["value"]

            # now = datetime.now()
            _logger.info(
                "%s %s ip=%s outdoor=%2d room=%2.1f mode=%s",
                name,
                on_off,
                ip,
                outdoor,
                room_temp,
                mode
                # target,
                # hw,
                # lwt,
                # offs,
            )

            payload = {
                "device_id": device_id,
                "room": name,
                "value": room_temp,  # random temp 20–25°C
                "unit": "C",
                "timestamp": datetime.now().isoformat()
            }
            topic = TOPIC_TEMPLATE.format(device_id=device_id)
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            client.username_pw_set(USERNAME, PASSWORD)
            client.connect(BROKER, PORT, TIMEOUT*10)
            client.publish(topic, json.dumps(payload), qos=1)
            client.disconnect()

        # API requests are limited to 200 per day
        # They suggest one per 10 minutes, which leaves around 50 for
        # actually controlling the system. Or perhaps downloading
        # consumption figures at the end of the day.
        time.sleep(TIMEOUT)
        print("----------")


def main():
    now = datetime.now()
    tstamp = now.strftime("%Y%m%d-%H%M")

    with gzip.open(filename="/tmp/daikin." + tstamp + ".log.gz", mode="wt") as zf:
        setup_logging(zf)
        monitor()


if __name__ == "__main__":
    main()
