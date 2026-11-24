# ProtocolCodec - newline-delimited JSON messages on the ESP32 side (LLD section 5).
# MicroPython-compatible: uses the built-in ``json`` module (ujson on older firmware).

try:
    import json
except ImportError:  # pragma: no cover - very old MicroPython builds
    import ujson as json


class ProtocolCodec:
    @staticmethod
    def decode(line):
        """bytes/str line -> dict. Raises ValueError on malformed input."""
        if isinstance(line, (bytes, bytearray)):
            line = line.decode("utf-8")
        msg = json.loads(line)
        if not isinstance(msg, dict) or "type" not in msg:
            raise ValueError("message must be a JSON object with a 'type'")
        return msg

    @staticmethod
    def decode_config(msg, mirror):
        mirror.load_config(msg["config"])

    @staticmethod
    def decode_call(msg, mirror):
        """Load fleet_state into the mirror; return the call as (req_id, time, src, target)."""
        r = msg["req"]
        mirror.update_telemetry(msg["fleet_state"])
        return int(r["id"]), float(r["time"]), int(r["src"]), int(r["target"])

    @staticmethod
    def encode_decision(req_id, elevator_id, route, cost, compute_ms):
        return json.dumps(
            {
                "type": "ALLOCATE_RES",
                "req_id": req_id,
                "elevator_id": elevator_id,
                "route": route,
                "cost": cost,
                "compute_ms": compute_ms,
            }
        )

    @staticmethod
    def encode_ack(n_elevators):
        return json.dumps({"type": "INIT_ACK", "elevators": n_elevators})

    @staticmethod
    def encode_error(req_id, code, message):
        return json.dumps({"type": "ERROR", "req_id": req_id, "code": code, "message": message})

    @staticmethod
    def encode_pong():
        return json.dumps({"type": "PONG"})
