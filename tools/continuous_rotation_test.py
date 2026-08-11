"""Continuous forward rotation test - 1 fraction per second"""
import time
import os

CMD_FILE = "collector_cmd.txt"
STATUS_FILE = "collector_status.txt"

STEP_SCALE = 1.0
BACKLASH_STEPS = 4
COMP = 1.0

ANGLES = [
    84,78,75,70,64,61,58,56,54,52,50,48,47,46,45,44,43,42,41,40,
    39,39,38,37,36,36,35,34,34,34,33,33,32,32,31,31,31,30,30,30,
    29,29,29,28,28,28,27,27,27,26,26,26,26,26,25,25,25,25,24,24,
    24,24,24,24,23,23,23,23,23,23,22,22,22,22,22,22,22,22,21,21,
    21,21,21,21,21,20,20
]

def compute_cumulative():
    cumulative = []
    total = 0.0
    for angle in ANGLES:
        total += angle * STEP_SCALE * COMP + BACKLASH_STEPS
        cumulative.append(round(total))
    return cumulative

def send_cmd(cmd):
    with open(CMD_FILE, 'w') as f:
        f.write(cmd)
    # wait for calibrate_collector.py to consume it
    while os.path.exists(CMD_FILE):
        time.sleep(0.1)

def wait_status(prefix, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, 'r') as f:
                s = f.read().strip()
            if s.startswith(prefix):
                return s
        time.sleep(0.1)
    return None

def main():
    cumulative = compute_cumulative()
    n_tubes = len(ANGLES)

    print(f"Continuous rotation test: {n_tubes} tubes, 1s interval")
    print(f"Settings: step_scale={STEP_SCALE}, backlash={BACKLASH_STEPS}")
    print()

    # HOME first
    print("Sending HOME...", flush=True)
    send_cmd("<HOME,111>")
    time.sleep(3)
    print("HOME done. Starting in 2s...", flush=True)
    time.sleep(2)

    max_tubes = 66  # T01-T66 only
    prev_steps = 0
    for i in range(min(n_tubes, max_tubes)):
        tube_num = i + 1
        target = cumulative[i]
        delta = target - prev_steps
        angle = ANGLES[i]

        # forward direction = negative delta
        cmd = f"<RUN,111,{-delta},{-delta},{-delta}>"
        send_cmd(cmd)
        print(f"T{tube_num:02d} | angle={angle:2d} | delta={delta:2d} | cumul={target:3d} | {cmd}", flush=True)

        prev_steps = target
        time.sleep(1)

    print()
    print(f"Done! Total steps: {cumulative[-1]}")
    print("Collector at last tube position. Send HOME manually to return.")

if __name__ == "__main__":
    main()
