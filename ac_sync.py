#!/usr/bin/env python3

"""Keep Daikin AC units in sync when outdoor temperature is high."""

import argparse
import logging
import time
from datetime import datetime, time as clock_time
from typing import Any, Optional


POLL_INTERVAL_SECONDS = 15 * 60
OUTDOOR_THRESHOLD_C = 29.0
MIN_OUTDOOR_TEMPERATURE_C = 27.0
COOLING_SETPOINT_C = 28.0
ROOM_OFF_THRESHOLD_OFFSET_C = 1.0
SOTAO_DEFAULT_ON_TEMPERATURE_C = 27.0
CLIMATE_CONTROL = "climateControl"
MASTER_DEVICE_NAME = "Sotao"
NIGHT_SKIP_DEVICE_NAME = "Suite"
NIGHT_SKIP_START = clock_time(21, 0)
NIGHT_SKIP_END = clock_time(9, 0)


_logger = logging.getLogger(__name__)


def characteristic_value(management_point: dict[str, Any], name: str) -> Any:
    characteristic = management_point.get(name)
    if isinstance(characteristic, dict):
        return characteristic.get("value")
    return None


def climate_control(device: dict[str, Any]) -> Optional[dict[str, Any]]:
    for management_point in device.get("managementPoints", []):
        if management_point.get("embeddedId") == CLIMATE_CONTROL:
            return management_point
    return None


def device_name(climate: Optional[dict[str, Any]]) -> str:
    if climate is None:
        return "<unknown>"
    return characteristic_value(climate, "name") or "<unknown>"


def outdoor_temperature(climate: dict[str, Any]) -> Optional[float]:
    sensory_data = characteristic_value(climate, "sensoryData") or {}
    outdoor = sensory_data.get("outdoorTemperature", {})
    value = outdoor.get("value") if isinstance(outdoor, dict) else None
    return float(value) if value is not None else None


def room_temperature(climate: dict[str, Any]) -> Optional[float]:
    sensory_data = characteristic_value(climate, "sensoryData") or {}
    room = sensory_data.get("roomTemperature", {})
    value = room.get("value") if isinstance(room, dict) else None
    return float(value) if value is not None else None


def cooling_setpoint(climate: dict[str, Any]) -> Optional[float]:
    return room_temperature_setpoint(climate, "cooling")


def room_temperature_setpoint(climate: dict[str, Any], mode: str) -> Optional[float]:
    temperature_control = characteristic_value(climate, "temperatureControl") or {}
    mode_control = (temperature_control.get("operationModes") or {}).get(mode) or {}
    room_temperature = (mode_control.get("setpoints") or {}).get("roomTemperature") or {}
    value = room_temperature.get("value")
    return float(value) if value is not None else None


def nested_value(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def fan_mode_value(climate: dict[str, Any], mode: str) -> Optional[str]:
    fan_control = characteristic_value(climate, "fanControl") or {}
    return nested_value(fan_control, "operationModes", mode, "fanSpeed", "currentMode", "value")


def fixed_fan_speed(climate: dict[str, Any], mode: str) -> Optional[int]:
    fan_control = characteristic_value(climate, "fanControl") or {}
    return nested_value(fan_control, "operationModes", mode, "fanSpeed", "modes", "fixed", "value")


def fan_direction_value(climate: dict[str, Any], mode: str, direction: str) -> Optional[str]:
    fan_control = characteristic_value(climate, "fanControl") or {}
    return nested_value(
        fan_control,
        "operationModes",
        mode,
        "fanDirection",
        direction,
        "currentMode",
        "value",
    )


def set_device(daikin: Any, gateway_id: str, management_path: str, **payload: Any) -> None:
    daikin.device = gateway_id
    daikin.patch(management_path, **payload)


def set_characteristic_value(management_point: dict[str, Any], name: str, value: Any) -> None:
    characteristic = management_point.get(name)
    if isinstance(characteristic, dict):
        characteristic["value"] = value


def set_room_temperature_setpoint(climate: dict[str, Any], mode: str, value: float) -> None:
    temperature_control = characteristic_value(climate, "temperatureControl") or {}
    room_temperature = nested_value(
        temperature_control,
        "operationModes",
        mode,
        "setpoints",
        "roomTemperature",
    )
    if isinstance(room_temperature, dict):
        room_temperature["value"] = value


def add_patch_if_needed(
    patches: list[tuple[str, str, dict[str, Any]]],
    label: str,
    path: str,
    current: Any,
    desired: Any,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    if desired is None or current == desired:
        return

    patches.append((label, path, payload or {"value": desired}))


def is_night_skip_time(now: Optional[datetime] = None) -> bool:
    current_time = (now or datetime.now()).time()
    return current_time >= NIGHT_SKIP_START or current_time < NIGHT_SKIP_END


def add_power_patch(
    patches: list[tuple[str, str, dict[str, Any]]],
    device_label: str,
    current: Any,
    desired: Any,
) -> None:
    if desired is None:
        return

    if (
        device_label.casefold() == NIGHT_SKIP_DEVICE_NAME.casefold()
        and is_night_skip_time()
    ):
        if desired != "off":
            _logger.info("%s power target overridden to off for the night window", device_label)
        desired = "off"

    if current == desired:
        return

    patches.append(
        (
            "power",
            f"{CLIMATE_CONTROL}/characteristics/onOffMode",
            {"value": desired},
        )
    )


def apply_patches(
    daikin: Any,
    gateway_id: str,
    name: str,
    patches: list[tuple[str, str, dict[str, Any]]],
    dry_run: bool,
    already_synced_message: str,
) -> None:
    if not patches:
        _logger.info(already_synced_message)
        return

    for label, path, payload in patches:
        if dry_run:
            _logger.info("dry-run: would set %s %s: %s", name, label, payload)
            continue

        _logger.info("setting %s %s: %s", name, label, payload)
        set_device(daikin, gateway_id, path, **payload)


def already_off_for_night_window(device_label: str) -> bool:
    return (
        device_label.casefold() == NIGHT_SKIP_DEVICE_NAME.casefold()
        and is_night_skip_time()
    )


def enforce_night_skip_device_off(
    daikin: Any,
    readings: list[tuple[dict[str, Any], dict[str, Any], Optional[float]]],
    dry_run: bool,
) -> None:
    if not is_night_skip_time():
        return

    for device, climate, _ in readings:
        name = device_name(climate)
        if name.casefold() != NIGHT_SKIP_DEVICE_NAME.casefold():
            continue

        patches: list[tuple[str, str, dict[str, Any]]] = []
        add_power_patch(
            patches,
            name,
            characteristic_value(climate, "onOffMode"),
            "off",
        )
        apply_patches(
            daikin,
            device["id"],
            name,
            patches,
            dry_run,
            "%s is already off for the night window" % name,
        )
        return

    _logger.warning("%s was not found; cannot enforce night power-off", NIGHT_SKIP_DEVICE_NAME)


def all_room_temperatures_below(
    readings: list[tuple[dict[str, Any], dict[str, Any], Optional[float]]],
    threshold: float,
) -> bool:
    if not readings:
        return False

    for _, climate, _ in readings:
        room = room_temperature(climate)
        if room is None:
            _logger.info(
                "%s room temperature is unavailable; not turning all units off",
                device_name(climate),
            )
            return False

        if room >= threshold:
            return False

    return True


def average_outdoor_temperature(
    readings: list[tuple[dict[str, Any], dict[str, Any], Optional[float]]]
) -> Optional[float]:
    if not readings:
        return None

    outdoor_temperatures = []
    for _, climate, outdoor in readings:
        if outdoor is None:
            _logger.info(
                "%s outdoor temperature is unavailable; not calculating room-off threshold",
                device_name(climate),
            )
            return None

        outdoor_temperatures.append(outdoor)

    return sum(outdoor_temperatures) / len(outdoor_temperatures)


def turn_all_devices_off(
    daikin: Any,
    readings: list[tuple[dict[str, Any], dict[str, Any], Optional[float]]],
    dry_run: bool,
) -> None:
    for device, climate, _ in readings:
        name = device_name(climate)
        patches: list[tuple[str, str, dict[str, Any]]] = []
        add_power_patch(
            patches,
            name,
            characteristic_value(climate, "onOffMode"),
            "off",
        )
        apply_patches(
            daikin,
            device["id"],
            name,
            patches,
            dry_run,
            "%s is already off" % name,
        )


def sync_sotao_defaults_if_warm(
    daikin: Any,
    readings: list[tuple[dict[str, Any], dict[str, Any], Optional[float]]],
    setpoint: float,
    dry_run: bool,
) -> None:
    for device, climate, _ in readings:
        if device_name(climate).casefold() != MASTER_DEVICE_NAME.casefold():
            continue

        room = room_temperature(climate)
        if room is None:
            _logger.info("%s room temperature is unavailable; not applying initial default sync", MASTER_DEVICE_NAME)
            return

        if room < SOTAO_DEFAULT_ON_TEMPERATURE_C:
            return

        _logger.info(
            "%s room temperature %.1f C is at or above %.1f C; applying default cooling sync",
            MASTER_DEVICE_NAME,
            room,
            SOTAO_DEFAULT_ON_TEMPERATURE_C,
        )
        sync_device(daikin, device, setpoint=setpoint, dry_run=dry_run)
        set_characteristic_value(climate, "operationMode", "cooling")
        set_characteristic_value(climate, "onOffMode", "on")
        set_room_temperature_setpoint(climate, "cooling", setpoint)
        return

    _logger.warning("%s was not found; cannot apply initial default sync", MASTER_DEVICE_NAME)


def sync_device(
    daikin: Any,
    device: dict[str, Any],
    setpoint: float,
    dry_run: bool,
) -> None:
    gateway_id = device["id"]
    climate = climate_control(device)
    name = device_name(climate)

    if climate is None:
        _logger.warning("%s has no climateControl management point; skipping", gateway_id)
        return

    patches: list[tuple[str, str, dict[str, Any]]] = []
    add_patch_if_needed(
        patches,
        "operation mode",
        f"{CLIMATE_CONTROL}/characteristics/operationMode",
        characteristic_value(climate, "operationMode"),
        "cooling",
    )
    add_patch_if_needed(
        patches,
        "cooling setpoint",
        f"{CLIMATE_CONTROL}/characteristics/temperatureControl",
        cooling_setpoint(climate),
        setpoint,
        {
            "path": "/operationModes/cooling/setpoints/roomTemperature",
            "value": setpoint,
        },
    )
    add_power_patch(
        patches,
        name,
        characteristic_value(climate, "onOffMode"),
        "on",
    )

    already_synced_message = "%s is already on cooling at %.1f C" % (name, setpoint)
    if already_off_for_night_window(name):
        already_synced_message = "%s is already off for the night window" % name

    apply_patches(
        daikin,
        gateway_id,
        name,
        patches,
        dry_run,
        already_synced_message,
    )


def sync_device_from_master(
    daikin: Any,
    device: dict[str, Any],
    master_climate: dict[str, Any],
    dry_run: bool,
) -> None:
    gateway_id = device["id"]
    climate = climate_control(device)
    name = device_name(climate)
    master_name = device_name(master_climate)

    if climate is None:
        _logger.warning("%s has no climateControl management point; skipping", gateway_id)
        return

    master_mode = characteristic_value(master_climate, "operationMode")
    master_power = characteristic_value(master_climate, "onOffMode")
    patches: list[tuple[str, str, dict[str, Any]]] = []

    add_patch_if_needed(
        patches,
        "operation mode",
        f"{CLIMATE_CONTROL}/characteristics/operationMode",
        characteristic_value(climate, "operationMode"),
        master_mode,
    )

    if master_mode is not None:
        master_setpoint = room_temperature_setpoint(master_climate, master_mode)
        add_patch_if_needed(
            patches,
            "%s room setpoint" % master_mode,
            f"{CLIMATE_CONTROL}/characteristics/temperatureControl",
            room_temperature_setpoint(climate, master_mode),
            master_setpoint,
            {
                "path": "/operationModes/%s/setpoints/roomTemperature" % master_mode,
                "value": master_setpoint,
            },
        )

        master_fan_mode = fan_mode_value(master_climate, master_mode)
        add_patch_if_needed(
            patches,
            "%s fan mode" % master_mode,
            f"{CLIMATE_CONTROL}/characteristics/fanControl",
            fan_mode_value(climate, master_mode),
            master_fan_mode,
            {
                "path": "/operationModes/%s/fanSpeed/currentMode" % master_mode,
                "value": master_fan_mode,
            },
        )

        if master_fan_mode == "fixed":
            master_fixed_fan_speed = fixed_fan_speed(master_climate, master_mode)
            add_patch_if_needed(
                patches,
                "%s fixed fan speed" % master_mode,
                f"{CLIMATE_CONTROL}/characteristics/fanControl",
                fixed_fan_speed(climate, master_mode),
                master_fixed_fan_speed,
                {
                    "path": "/operationModes/%s/fanSpeed/modes/fixed" % master_mode,
                    "value": master_fixed_fan_speed,
                },
            )

        for direction in ("horizontal", "vertical"):
            master_direction = fan_direction_value(master_climate, master_mode, direction)
            add_patch_if_needed(
                patches,
                "%s %s fan direction" % (master_mode, direction),
                f"{CLIMATE_CONTROL}/characteristics/fanControl",
                fan_direction_value(climate, master_mode, direction),
                master_direction,
                {
                    "path": "/operationModes/%s/fanDirection/%s/currentMode"
                    % (master_mode, direction),
                    "value": master_direction,
                },
            )

    add_power_patch(
        patches,
        name,
        characteristic_value(climate, "onOffMode"),
        master_power,
    )

    already_synced_message = "%s already matches %s" % (name, master_name)
    if already_off_for_night_window(name):
        already_synced_message = "%s is already off for the night window" % name

    apply_patches(
        daikin,
        gateway_id,
        name,
        patches,
        dry_run,
        already_synced_message,
    )


def sync_once(
    daikin: Any,
    threshold: float,
    setpoint: float,
    master_name: str,
    dry_run: bool,
) -> None:
    devices = daikin.get("gateway-devices")
    readings = []

    for device in devices:
        climate = climate_control(device)
        if climate is None:
            _logger.warning("%s has no climateControl management point", device.get("id"))
            continue

        outdoor = outdoor_temperature(climate)
        readings.append((device, climate, outdoor))
        _logger.info(
            "%s: power=%s mode=%s room=%s C outdoor=%s C cooling_setpoint=%s C",
            device_name(climate),
            characteristic_value(climate, "onOffMode"),
            characteristic_value(climate, "operationMode"),
            room_temperature(climate),
            outdoor,
            cooling_setpoint(climate),
        )

    sync_sotao_defaults_if_warm(daikin, readings, setpoint=setpoint, dry_run=dry_run)

    hot_readings = [
        (device, climate, outdoor)
        for device, climate, outdoor in readings
        if outdoor is not None and outdoor > threshold
    ]

    if not hot_readings:
        average_outdoor = average_outdoor_temperature(readings)
        if average_outdoor is not None:
            room_off_threshold = max(average_outdoor - ROOM_OFF_THRESHOLD_OFFSET_C, MIN_OUTDOOR_TEMPERATURE_C)
        else:
            room_off_threshold = None

        if room_off_threshold is not None and all_room_temperatures_below(readings, room_off_threshold):
            _logger.info(
                "no outdoor readings are above %.1f C and all rooms are below %.1f C "
                "(average outdoor %.1f C - %.1f C); turning all units off",
                threshold,
                room_off_threshold,
                average_outdoor,
                ROOM_OFF_THRESHOLD_OFFSET_C,
            )
            turn_all_devices_off(daikin, readings, dry_run=dry_run)
            return

        enforce_night_skip_device_off(daikin, readings, dry_run=dry_run)
        _logger.info("outdoor temperature is not above %.1f C; no heat sync needed", threshold)
        return

    hottest = max(outdoor for _, _, outdoor in hot_readings if outdoor is not None)
    _logger.info(
        "outdoor temperature %.1f C is above %.1f C; checking sync strategy",
        hottest,
        threshold,
    )

    master_reading = next(
        (
            (device, climate, outdoor)
            for device, climate, outdoor in readings
            if device_name(climate).casefold() == master_name.casefold()
        ),
        None,
    )
    if master_reading is not None:
        master_device, master_climate, _ = master_reading
        if characteristic_value(master_climate, "onOffMode") == "on":
            _logger.info("%s is on; copying its options to the other devices", master_name)
            for device, _, _ in readings:
                if device["id"] == master_device["id"]:
                    continue
                sync_device_from_master(daikin, device, master_climate, dry_run=dry_run)
            return

        _logger.info("%s is not on; using default cooling %.1f C sync", master_name, setpoint)
    else:
        _logger.warning("master device %s was not found; using default cooling %.1f C sync", master_name, setpoint)

    for device, _, _ in readings:
        sync_device(daikin, device, setpoint=setpoint, dry_run=dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll Daikin devices and sync AC settings when it is hot outside."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=POLL_INTERVAL_SECONDS,
        help="seconds between checks; default: 900",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=OUTDOOR_THRESHOLD_C,
        help="outdoor temperature threshold in Celsius; default: 28",
    )
    parser.add_argument(
        "--setpoint",
        type=float,
        default=COOLING_SETPOINT_C,
        help="cooling setpoint in Celsius; default: 27",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single sync cycle and exit",
    )
    parser.add_argument(
        "--master-name",
        default=MASTER_DEVICE_NAME,
        help="device name to copy settings from when it is on; default: Sotao",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log intended changes without sending PATCH requests",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    from daikin import Daikin

    daikin = Daikin()

    while True:
        _logger.info("starting AC sync cycle at %s", datetime.now().isoformat(timespec="seconds"))

        try:
            sync_once(
                daikin,
                threshold=args.threshold,
                setpoint=args.setpoint,
                master_name=args.master_name,
                dry_run=args.dry_run,
            )
        except Exception:
            _logger.exception("AC sync cycle failed")
            if args.once:
                raise

        if args.once:
            return

        _logger.info("sleeping for %d seconds", args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _logger.info("stopped")
