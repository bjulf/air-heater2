"""Publishing process data over MQTT, and receiving it safely.

    from airheater.telemetry import MqttConfig, MqttPublisher

``payload`` is the trust boundary: everything arriving from a broker goes
through :func:`~airheater.telemetry.payload.decode_measurement` before it is
used. It has no third-party dependencies, so the validation can be tested
without paho-mqtt installed.

``mqtt`` needs paho-mqtt and is imported on demand, so the control application
runs without it when publishing is not wanted.
"""

from .config import MqttConfig
from .payload import (
    SCHEMA_VERSION,
    InvalidPayload,
    Measurement,
    decode_measurement,
    encode_sample,
)

__all__ = [
    "MqttConfig",
    "Measurement",
    "InvalidPayload",
    "SCHEMA_VERSION",
    "encode_sample",
    "decode_measurement",
]
