"""Fraction Collector Calibration Helper - background serial controller"""
import serial
import time
import os
import sys

PORT = "COM3"
BAUD = 2000000
CMD_FILE = "collector_cmd.txt"
STATUS_FILE = "collector_status.txt"

SETUP_CMDS = [
    "<SET_ACCEL,111,640.0,640.0,640.0>",
    "<SET_SPEED,111,1600.0,1600.0,1600.0>",
]

def write_status(msg):
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        f.write(msg)
    print(msg, flush=True)

try:
    write_status("CONNECTING")
    ser = serial.Serial(port=PORT, baudrate=BAUD,
                        parity=serial.PARITY_NONE,
                        stopbits=serial.STOPBITS_ONE,
                        bytesize=serial.EIGHTBITS,
                        timeout=1)
    print("Waiting 3s for Arduino reset...", flush=True)
    time.sleep(3)

    for cmd in SETUP_CMDS:
        ser.write(cmd.encode())
        ser.reset_input_buffer()
        time.sleep(0.1)
        print(f"Setup: {cmd}", flush=True)

    write_status("READY")

    while True:
        if os.path.exists(CMD_FILE):
            with open(CMD_FILE, 'r') as f:
                cmd = f.read().strip()
            os.remove(CMD_FILE)
            if cmd == "QUIT":
                write_status("QUIT")
                break
            ser.write(cmd.encode())
            ser.reset_input_buffer()
            write_status(f"SENT:{cmd}")
        time.sleep(0.3)

    ser.close()
    print("Serial closed.", flush=True)

except Exception as e:
    write_status(f"ERROR:{e}")
    print(f"Error: {e}", flush=True)
    sys.exit(1)
