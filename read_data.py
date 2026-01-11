#!/usr/bin/env python3
"""
Script to read temperature, humidity, and battery data from Xiaomi LYWSD03MMC sensors
Uses the bleak library for cross-platform BLE support
"""

import asyncio
import struct
from bleak import BleakScanner, BleakClient

XIAOMI_DEVICES = {
    "8E54CB01-FED7-771B-181D-BE97084613F5": "THSotao",
    "BE2DA4F4-6404-24E4-01E7-606AA84AE025": "THQuarto",
    "9F34D2FA-DA7B-0B2C-8EDD-A9AF9A4319CB": "THSala",
    "ABB9B2E0-F4C0-CD44-D099-5ABA1F52C6E6": "THSuite"
}


MAC_TO_NAMES = {
    "A4:C1:38:E7:B4:DA": "THSotao",
    "A4:C1:38:9F:50:59": "THSuite",
    "A4:C1:38:6A:16:C6": "THQuarto",
    "A4:C1:38:C6:C4:CF": "THSala"
}

ENV_KEY = '0000181a-0000-1000-8000-00805f9b34fb'

# Characteristic UUIDs for LYWSD03MMC
TEMP_HUMIDITY_UUID = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"  # Temperature & Humidity
BATTERY_UUID = "ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6"  # Battery level

async def find_lywsd03mmc_devices(scan_duration=5.0):
    """Scan for LYWSD03MMC devices"""
    print(f"Scanning for LYWSD03MMC devices for {scan_duration} seconds...")
    devices = await BleakScanner.discover(timeout=scan_duration)
    
    lywsd_devices = []
    for device in devices:
        if device.name and "LYWSD03MMC" in device.name:
            lywsd_devices.append(device)
            print(f"Found: {device.name} - {device.address}")
    
    return lywsd_devices

async def read_sensor_data(address):
    """Connect to device and read sensor data"""
    #print(f"Connecting to {address}...")
    
    async with BleakClient(address, timeout=30.0) as client:
        if not client.is_connected:
            print(f"Failed to connect {address}")
            return
        
        #print("Connected successfully!")
        
        # Read temperature and humidity
        try:
            temp_hum_data = await client.read_gatt_char(TEMP_HUMIDITY_UUID)
            # Data format: temperature (2 bytes), humidity (1 byte)
            temp_raw, hum_raw = struct.unpack('<HB', temp_hum_data[:3])
            temperature = temp_raw / 100.0  # Temperature in Celsius
            humidity = hum_raw  # Humidity in %
            
            print(f"{temperature:.2f}°C {humidity}%")
        except Exception as e:
            print(f"Error reading temperature/humidity: {e}")
        
        # Read battery level
        try:
            battery_data = await client.read_gatt_char(BATTERY_UUID)
            battery = struct.unpack('<B', battery_data)[0]
            print(f"Battery: {battery}%")
        except Exception as e:
            print(f"Error reading battery: {e}")



def decode_ble_packet(adv):
    """
    Decode a BLE packet with structure:
    [MAC (6 bytes)] [Temperature (2 bytes, °C x10)] [Humidity (1 byte, %)] [Battery (1 byte, %)]
    
    Returns a dictionary with MAC, temperature, humidity, battery.
    """
    packet_bytes = adv.service_data[ENV_KEY]
    if len(packet_bytes) < 10:
        raise ValueError("Packet too short to decode")

    # 1. MAC address
    mac_bytes = packet_bytes[0:6]
    mac = ':'.join(f'{b:02X}' for b in mac_bytes)

    # 2. Temperature (2 bytes, int16, little-endian, °C ×10)
    temp_bytes = packet_bytes[6:8]
    temp_raw = int.from_bytes(temp_bytes, byteorder='big', signed=True)
    temperature = temp_raw / 10.0  # °C

    # 3. Humidity (1 byte)
    humidity = packet_bytes[8]

    # 4. Battery (1 byte)
    battery = packet_bytes[9]

    local_name = MAC_TO_NAMES.get(mac, adv.local_name)

    result = {
        "name": local_name,
        "mac": mac,
        "temperature_c": temperature,
        "humidity_percent": humidity,
        "battery_percent": battery
    }
    print(result)
    return result



async def scan_and_dump():
    print("Scanning 5 secs for advertising BLE devices ...", flush=True)
    devices = await BleakScanner.discover(timeout=5, return_adv=True)
    for device, adv in devices.values():
        #print(f"Metadata: {device}", flush=True)
        # print(device, flush=True)
        #if device.name and "LYWSD03MMC" in device.name:
        if device.address in XIAOMI_DEVICES.keys() or device.address in MAC_TO_NAMES.keys():
            print(f"{device.address}  ({XIAOMI_DEVICES[device.address]})", flush=True)
            try:
                decode_ble_packet(adv)
            except: 
                print(adv, flush=True)
            print("----")


async def main():
    """Main function"""
    # Find LYWSD03MMC devices
    devices = []
    #devices = await find_lywsd03mmc_devices(scan_duration=5.0)

    await scan_and_dump()
    # Read data from each found device
    for device in devices:
        if device.address in XIAOMI_DEVICES:
            try:
                await read_sensor_data(device.address)
            except Exception as e:
                print(f"Error reading from {device.address}: {e}")
            print("-" * 40)

if __name__ == "__main__":
    # Install required package:
    # pip install bleak
    
    try:
        while True:
            asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped by user")