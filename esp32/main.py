# ESP32 entry point (MicroPython runs main.py automatically after boot).
#
# Copy config_example.py to config.py, fill in your Wi-Fi credentials, then upload all esp32/*.py files.
# The serial console (115200 baud) shows the IP address and one log line per allocation.

import gc

from edge_node import EdgeNode
from wireless_server import WirelessServer

try:
    import config
except ImportError:
    raise SystemExit("config.py missing: copy config_example.py to config.py and set WIFI_SSID / WIFI_PASSWORD")


def run():
    gc.collect()
    node = EdgeNode(verbose=getattr(config, "VERBOSE", True))
    server = WirelessServer(port=getattr(config, "PORT", 5000), handler=node.handle_line, verbose=True)
    ip = server.begin(config.WIFI_SSID, config.WIFI_PASSWORD)
    print("=== Smart Elevator EdgeAllocator ready: set esp32_bridge.host = %s, port = %d ===" % (ip, server.port))
    print("free heap after init: %d bytes" % gc.mem_free())
    while True:
        server.poll_client()
        gc.collect()


run()
