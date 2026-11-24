# ESP32 Edge Allocator (MicroPython)

This folder is the firmware for the hardware-in-the-loop (HIL) mode. The ESP32 runs the same
**MC-DDI** allocation algorithm as the PC (`MarginalCostDDDispatcher`). The PC simulator sends it every
call over Wi-Fi as newline-delimited JSON, and it sends back the chosen elevator and route.

| File | Role (LLD §5) |
| :--- | :--- |
| `main.py` | Runs at boot: joins Wi-Fi, starts the TCP server, logs every decision to the serial console |
| `wireless_server.py` | `WirelessServer`: Wi-Fi station plus a single-client TCP line server (`begin`, `poll_client`, `send_response`) |
| `protocol_codec.py` | `ProtocolCodec`: decodes `INIT_CONFIG` / `ALLOCATE_REQ`, encodes `ALLOCATE_RES` / `ERROR` |
| `fleet_state_mirror.py` | `FleetStateMirror`: fixed-size digital twin of the fleet (`load_config`, `update_telemetry`, `commit_assignment`) |
| `edge_allocator.py` | `EdgeAllocator`: MC-DDI ported statement by statement, with all buffers preallocated |
| `edge_node.py` | Connects the codec, mirror and allocator: one request line in, one reply line out |
| `config_example.py` | Wi-Fi settings template. Copy it to `config.py` (git-ignored) |

## 1. What you need
* An ESP32 dev board (classic ESP32-WROOM / WROVER; S2/S3/C3 boards work too, see the note in step 2)
* A USB data cable, plus the board's USB-UART driver if your OS needs one (CP210x or CH340)
* The PC and the ESP32 on the **same 2.4 GHz Wi-Fi network**
* Host tools, installed into the project's `.venv`:
  ```bash
  uv pip install --python .venv/bin/python esptool mpremote
  ```

## 2. Flash MicroPython (once per board)
1. Download the latest stable **MicroPython ≥ 1.20** firmware for your board from
   <https://micropython.org/download/> (for a classic board: `ESP32_GENERIC`).
2. Find the serial port: `ls /dev/tty.usbserial-* /dev/tty.SLAB_USBtoUART /dev/ttyUSB*` (macOS/Linux) or
   Device Manager → COM port (Windows).
3. Erase the flash, then write the firmware (hold **BOOT** while it connects if the board doesn't do it automatically):
   ```bash
   .venv/bin/esptool.py --chip esp32 --port /dev/tty.usbserial-0001 erase_flash
   .venv/bin/esptool.py --chip esp32 --port /dev/tty.usbserial-0001 --baud 460800 write_flash -z 0x1000 ESP32_GENERIC-<version>.bin
   ```
   ESP32-S3 and ESP32-C3 use offset `0x0` instead of `0x1000`. Pass the matching `--chip`, and check the
   download page for your board.

## 3. Upload the edge allocator
```bash
cp esp32/config_example.py esp32/config.py     # then edit WIFI_SSID / WIFI_PASSWORD (and PORT if needed)
.venv/bin/mpremote connect /dev/tty.usbserial-0001 cp esp32/edge_allocator.py esp32/fleet_state_mirror.py \
    esp32/protocol_codec.py esp32/edge_node.py esp32/wireless_server.py esp32/config.py esp32/main.py :
.venv/bin/mpremote connect /dev/tty.usbserial-0001 reset
```

## 4. Read the IP address from the serial console (115200 baud)
```bash
.venv/bin/mpremote connect /dev/tty.usbserial-0001 repl     # Ctrl-] to exit
```
You should see:
```
=== Smart Elevator EdgeAllocator ready: set esp32_bridge.host = 192.168.1.105, port = 5000 ===
free heap after init: ...
client connected: ...
INIT_CONFIG: 3 elevators, floors -2..15
ALLOC req=0 14->13 => E0 cost=20.50 in 4.812 ms
```
Every allocation is logged with its computation time (FR-HW-03).

## 5. Run the simulator against the board
Either pass the address on the command line:
```bash
python -m src.main --config simulation_config.json --requests data/requests.csv --esp32 192.168.1.105:5000
```
or set it in `simulation_config.json`, in which case `ESP32DispatcherProxy` is used automatically:
```json
"esp32_bridge": { "enabled": true, "transport": "WIFI_TCP", "host": "192.168.1.105", "port": 5000, "timeout_ms": 500 }
```
The summary then reports `"execution_mode": "ESP32"`, and the console prints the link statistics
(`sent`, `ok`, `timeouts`, `reconnects`, `fallbacks`).

**If the link fails:** after a timeout or disconnect, the proxy reconnects, re-sends `INIT_CONFIG`
and retries once. If the board still can't answer, that call is computed by the identical local MC-DDI,
so the simulation never hangs or drops a passenger.

## 6. Hardware equivalence test
```bash
ESP32_HOST=192.168.1.105 ESP32_PORT=5000 .venv/bin/python -m pytest tests/test_esp32_bridge.py -m hil -v
```
It replays the request stream through the board and asserts 100% `elevator_id` parity with the PC
over the first 50 calls, p99 round trip below `timeout_ms`, and no fallbacks.

Without hardware, `tests/test_esp32_bridge.py` runs these same firmware files under CPython as a local
TCP mock ESP32. It checks bit-for-bit decision parity (elevator, route and cost) over:
* the 50-request replay,
* four full scenarios,
* an overload and fallback burst,
* 400 randomized fleet states.

## Protocol (one JSON object per line)
| Direction | `type` | Body |
| :--- | :--- | :--- |
| PC → ESP32 | `INIT_CONFIG` | `config`: floors, MC-DDI parameters, per-car kinematics |
| ESP32 → PC | `INIT_ACK` | `elevators` |
| PC → ESP32 | `ALLOCATE_REQ` | `req {id,time,src,target}`, `fleet_state [{id,t,pos,spd,w,vib,obs,op,cap,queue,reqs}]` |
| ESP32 → PC | `ALLOCATE_RES` | `req_id`, `elevator_id`, `route`, `cost`, `compute_ms` |
| ESP32 → PC | `ERROR` | `req_id`, `code` (`NOT_INITIALIZED`, `CAPACITY_EXCEEDED`, `BAD_REQUEST`, `UNSUPPORTED`), `message` |
| either | `PING` / `PONG` | heartbeat |

Hidden passenger weights are never sent. The board only sees the load-sensor reading.

## Limits and notes
* **Memory:** limits are fixed at compile time in `fleet_state_mirror.py`: `MAX_ELEVATORS = 8`,
  `MAX_STOPS = 32` stops per car, and `MAX_REQS = 32` active requests per car. Every buffer is
  allocated once at boot, and a larger payload is answered with `ERROR CAPACITY_EXCEEDED` instead of
  growing the heap.
* **Floating-point precision:** the standard ESP32 MicroPython builds use **32-bit floats**. Car choices
  therefore match the PC except in near-ties closer than float32 resolution, and the reported `cost` and
  `compute_ms` can differ in the last digits. On the host (64-bit floats) parity is exact. To get
  bit-identical costs on hardware, build MicroPython with
  `MICROPY_FLOAT_IMPL = MICROPY_FLOAT_IMPL_DOUBLE`.
* **One client at a time** (the simulator), on TCP port 5000 by default.
* **Bluetooth:** BLE (`BLEGattTransport`) is a stub on the PC side. Wi-Fi TCP is the supported link (HLD §6).
