"""Wire format for process data, and the validation of it.

This module is the trust boundary of the system. Anything arriving from an
MQTT broker is data from the network, not from your control application: any
client that can reach the broker and is allowed to publish on the topic can
send whatever it likes, and a compromised broker can rewrite messages in
flight. So the decoder treats every payload as hostile until it has been
checked, and :func:`decode_measurement` is the only way into the rest of the
system.

What the decoder enforces:

  * a size limit, before parsing - a decompression or allocation bomb is
    cheapest to stop at the door;
  * JSON only, parsed with ``json.loads``. Never ``pickle``, ``eval`` or
    ``yaml.load``: all three execute code from the payload;
  * an exact set of required fields with the right types;
  * finite numbers only - NaN and Infinity survive a JSON round trip in
    Python and go on to poison any average computed over them;
  * plausible physical ranges, so a fault or a forged message shows up as a
    rejected payload instead of a plot with a 10^9 degC spike in it;
  * a constrained ``source`` string, because that value ends up in a database
    and in log lines.

Rejected payloads raise :class:`InvalidPayload`. The caller counts and logs
them rather than crashing: a bad message must not take down the collector.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = [
    "SCHEMA_VERSION",
    "MAX_PAYLOAD_BYTES",
    "InvalidPayload",
    "Measurement",
    "encode_sample",
    "decode_measurement",
]

SCHEMA_VERSION = 1

# A sample encodes to roughly 200 bytes. 4 KiB leaves room for extra fields
# without accepting anything that could only be an attempt at something else.
MAX_PAYLOAD_BYTES = 4096

# Source names identify a rig or control application. Keep them boring: this
# value reaches a database, log files and possibly a web page.
_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# Generous physical bounds. Not a calibration - just wide enough to pass
# anything the real process can produce and narrow enough that a forged or
# corrupt value is caught.
_LIMITS = {
    "t": (0.0, 365 * 24 * 3600.0),      # loop uptime [s]
    "setpoint": (-100.0, 300.0),        # [degC]
    "measured": (-100.0, 300.0),
    "filtered": (-100.0, 300.0),
    "error": (-400.0, 400.0),
    "u": (-100.0, 100.0),               # [V]
    "jitter": (-60.0, 3600.0),          # [s]
}

_REQUIRED = ("k", "t", "setpoint", "measured", "filtered", "u")


class InvalidPayload(ValueError):
    """A payload failed validation and must not be stored or acted on."""


@dataclass
class Measurement:
    """One validated sample, ready to store."""

    source: str
    k: int
    t: float
    setpoint: float
    measured: float
    filtered: float
    error: float
    u: float
    saturated: bool
    jitter: float
    schema: int = SCHEMA_VERSION
    received_at: float = field(default_factory=time.time)

    def as_row(self) -> Dict[str, Any]:
        """Flat dict for the storage layer. Keys match the table columns."""
        return {
            "source": self.source,
            "k": self.k,
            "t": self.t,
            "setpoint": self.setpoint,
            "measured": self.measured,
            "filtered": self.filtered,
            "error": self.error,
            "u": self.u,
            "saturated": int(self.saturated),
            "jitter": self.jitter,
            "schema": self.schema,
            "received_at": self.received_at,
        }


def encode_sample(sample, source: str) -> bytes:
    """Encode a :class:`~airheater.control.loop.Sample` for publishing.

    Parameters
    ----------
    sample:
        The sample from the control loop.
    source:
        Identifies the publishing rig or application, e.g. ``"pi-airheater-1"``.
        Validated here so a bad name is caught at the publisher rather than
        rejected by every subscriber.
    """
    _check_source(source)
    body = dict(sample.as_dict())
    body["schema"] = SCHEMA_VERSION
    body["source"] = source
    # separators: no padding whitespace, since this goes out once per sample.
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def decode_measurement(payload: bytes, source: Optional[str] = None) -> Measurement:
    """Validate a received payload and turn it into a :class:`Measurement`.

    Parameters
    ----------
    payload:
        Raw bytes from the broker.
    source:
        Optional source taken from the topic. When given it must match the
        ``source`` field in the body, which stops a client publishing on its
        own topic from attributing data to another rig.

    Raises
    ------
    InvalidPayload
        On anything unexpected. The message is then dropped.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise InvalidPayload("payload must be bytes, got {0}".format(type(payload).__name__))
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise InvalidPayload(
            "payload too large: {0} bytes (limit {1})".format(len(payload), MAX_PAYLOAD_BYTES)
        )

    try:
        text = bytes(payload).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidPayload("payload is not valid UTF-8") from exc

    try:
        body = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InvalidPayload("payload is not valid JSON: {0}".format(exc)) from exc

    if not isinstance(body, dict):
        raise InvalidPayload("payload must be a JSON object, got {0}".format(type(body).__name__))

    schema = body.get("schema", SCHEMA_VERSION)
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise InvalidPayload("schema must be an integer")
    if schema != SCHEMA_VERSION:
        raise InvalidPayload(
            "unsupported schema version {0} (this collector speaks {1})".format(
                schema, SCHEMA_VERSION
            )
        )

    body_source = body.get("source")
    if body_source is None:
        raise InvalidPayload("missing field: source")
    _check_source(body_source)
    if source is not None and body_source != source:
        # The topic said one rig, the body claims another. Trust neither.
        raise InvalidPayload(
            "source mismatch: topic says {0!r}, body says {1!r}".format(source, body_source)
        )

    for name in _REQUIRED:
        if name not in body:
            raise InvalidPayload("missing field: {0}".format(name))

    k = body["k"]
    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
        raise InvalidPayload("k must be a non-negative integer, got {0!r}".format(k))

    values = {}
    for name, (lo, hi) in _LIMITS.items():
        if name not in body:
            # Only the optional ones can be missing; _REQUIRED was checked above.
            values[name] = 0.0
            continue
        values[name] = _check_number(name, body[name], lo, hi)

    saturated = body.get("saturated", False)
    if not isinstance(saturated, bool):
        raise InvalidPayload("saturated must be a boolean, got {0!r}".format(saturated))

    return Measurement(
        source=body_source,
        k=k,
        t=values["t"],
        setpoint=values["setpoint"],
        measured=values["measured"],
        filtered=values["filtered"],
        error=values["error"],
        u=values["u"],
        saturated=saturated,
        jitter=values["jitter"],
        schema=schema,
    )


def _check_source(source: Any) -> None:
    if not isinstance(source, str):
        raise InvalidPayload("source must be a string, got {0}".format(type(source).__name__))
    if not _SOURCE_PATTERN.match(source):
        # Deliberately does not echo the whole value: it is untrusted, and log
        # files are read by people and sometimes rendered in web pages.
        raise InvalidPayload(
            "source must be 1-64 chars of [A-Za-z0-9_.-] and start alphanumeric "
            "(got {0} chars)".format(len(source))
        )


def _check_number(name: str, value: Any, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidPayload(
            "{0} must be a number, got {1}".format(name, type(value).__name__)
        )
    value = float(value)
    if not math.isfinite(value):
        # json.loads accepts NaN and Infinity by default; both would survive
        # into the database and corrupt every aggregate computed over them.
        raise InvalidPayload("{0} must be finite, got {1}".format(name, value))
    if not (lo <= value <= hi):
        raise InvalidPayload(
            "{0} out of range: {1} not in [{2}, {3}]".format(name, value, lo, hi)
        )
    return value
