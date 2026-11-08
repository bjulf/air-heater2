"""MQTT publisher and subscriber for air heater process data.

Topic layout, one level per source so several rigs share a broker::

    airheater/data/<source>          samples, QoS 1
    airheater/data/<source>/status   "online" / "offline", retained

The status topic is registered as the connection's last will, so the broker
publishes ``offline`` by itself if the Pi loses power or the network drops.
Without it a dead control application and an idle one look identical to the
collector, which is exactly the case you want to notice.

Written against paho-mqtt 2.x. The callback API changed in 2.0; this module
asks for ``CallbackAPIVersion.VERSION2`` explicitly rather than relying on the
default, which is what the deprecation warning in 2.x is about.

Testing without a broker
------------------------
The message handling is separated from the transport. :meth:`MqttPublisher.encode`
and :meth:`MqttSubscriber.handle_message` are ordinary methods with no network
in them, so the whole publish-validate-store chain can be tested end to end.
``client_factory`` additionally allows a fake client to be injected for the
connection-level wiring.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from .config import MqttConfig
from .payload import InvalidPayload, Measurement, decode_measurement, encode_sample

__all__ = ["MqttPublisher", "MqttSubscriber", "build_client"]

log = logging.getLogger(__name__)

STATUS_ONLINE = b"online"
STATUS_OFFLINE = b"offline"


def build_client(config: MqttConfig):
    """Create and configure a real paho-mqtt client from a configuration."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise ImportError(
            "paho-mqtt is required for MQTT: pip install paho-mqtt"
        ) from exc

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.client_id,
        protocol=mqtt.MQTTv5,
    )
    if config.username:
        client.username_pw_set(config.username, config.password)
    if config.tls:
        client.tls_set(
            ca_certs=config.ca_certs,
            certfile=config.certfile,
            keyfile=config.keyfile,
        )
        if config.tls_insecure:
            # Encryption without authentication. config.warnings() says so out loud.
            client.tls_insecure_set(True)
    return client


class _Endpoint:
    """Shared connection handling for the publisher and the subscriber."""

    def __init__(self, config: MqttConfig, client_factory: Optional[Callable] = None) -> None:
        self.config = config
        self._factory = client_factory or build_client
        self.client = self._factory(config)
        self.connected = threading.Event()
        self._closed = False

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    # -- callbacks (paho 2.x VERSION2 signatures) ------------------------

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            log.error("MQTT connection refused: %s", reason_code)
            return
        log.info("connected to %s:%s", self.config.host, self.config.port)
        self.connected.set()
        self._after_connect()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self.connected.clear()
        if self._closed:
            return
        # paho reconnects by itself while the network loop runs; this is a
        # notice, not an error to act on.
        log.warning("disconnected from broker (%s); will retry", reason_code)

    def _after_connect(self) -> None:
        """Hook for subclasses: re-publish status, re-subscribe, and so on."""

    # -- lifecycle -------------------------------------------------------

    def connect(self, timeout: float = 10.0) -> bool:
        """Connect and start the network loop. Never raises on a bad broker.

        Returns True if the broker acknowledged the connection within
        ``timeout``. False means it did not - the host is down, the name does
        not resolve, the certificate was rejected - and paho keeps retrying in
        the background, so the caller can carry on and check later.

        This uses ``connect_async`` rather than ``connect`` deliberately.
        paho's blocking ``connect`` performs the TCP connect inline and raises
        ``ConnectionRefusedError`` or ``socket.gaierror`` on failure, which
        would propagate into the control application and stop it controlling a
        heater because a logging server was unavailable. ``connect_async``
        hands the whole thing to the network loop, which retries with backoff.
        """
        for note in self.config.warnings():
            log.warning("SECURITY: %s", note)
        try:
            self.client.reconnect_delay_set(min_delay=1, max_delay=60)
            self.client.connect_async(
                self.config.host, self.config.port, self.config.keepalive
            )
            self.client.loop_start()
        except Exception as exc:
            # Bad port, bad TLS material and the like are raised immediately
            # even by connect_async. Still not the control loop's problem.
            log.error("could not start MQTT client: %r", exc)
            return False
        return self.connected.wait(timeout)

    def close(self) -> None:
        """Disconnect cleanly. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            self._before_close()
        finally:
            self.client.disconnect()
            self.client.loop_stop()

    def _before_close(self) -> None:
        """Hook for subclasses: say goodbye before the socket goes away."""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class MqttPublisher(_Endpoint):
    """Publishes control loop samples, one message per sample.

    Use it as the ``on_sample`` callback of
    :func:`~airheater.control.loop.run_control_loop`::

        with MqttPublisher(config) as pub:
            run_control_loop(io, pi, 28.0, Ts, on_sample=pub, ...)

    Publishing failures are logged and swallowed. That is deliberate: the loop
    is controlling a heater, and losing the broker must not stop it. The
    dropped count is kept so the loss is visible rather than silent.

    Parameters
    ----------
    config:
        Broker settings, including the topic and the source name.
    every:
        Publish one sample in every ``every``. Use it to put data on the
        network at a sensible rate while the loop runs faster; at Ts = 0.1 s,
        ``every=10`` gives one message per second.
    """

    def __init__(
        self,
        config: MqttConfig,
        every: int = 1,
        client_factory: Optional[Callable] = None,
    ) -> None:
        if every < 1:
            raise ValueError("every must be >= 1")
        self.every = int(every)
        self.published = 0
        self.dropped = 0
        super().__init__(config, client_factory)

        # Registered before connecting, so the broker holds it from the start.
        self.client.will_set(
            self.config.status_topic, STATUS_OFFLINE, qos=1, retain=True
        )

    def _after_connect(self) -> None:
        # Retained, so a collector that starts later still learns we are up.
        self.client.publish(self.config.status_topic, STATUS_ONLINE, qos=1, retain=True)

    def _before_close(self) -> None:
        try:
            self.client.publish(
                self.config.status_topic, STATUS_OFFLINE, qos=1, retain=True
            )
        except Exception as exc:  # pragma: no cover - broker already gone
            log.warning("could not publish offline status: %r", exc)

    def encode(self, sample) -> bytes:
        """Encode a sample without publishing it. No network involved."""
        return encode_sample(sample, self.config.source)

    def publish(self, sample) -> bool:
        """Publish one sample. Returns True if it was handed to the client."""
        if sample.k % self.every:
            return False
        try:
            payload = self.encode(sample)
            info = self.client.publish(
                self.config.data_topic, payload, qos=self.config.qos
            )
            rc = getattr(info, "rc", 0)
            if rc != 0:
                self.dropped += 1
                log.warning("publish failed with rc=%s", rc)
                return False
            self.published += 1
            return True
        except Exception as exc:
            # Never let a telemetry problem reach the control loop.
            self.dropped += 1
            log.warning("publish failed: %r", exc)
            return False

    # Lets the publisher be passed directly as on_sample.
    __call__ = publish


class MqttSubscriber(_Endpoint):
    """Receives samples from one or more sources and hands them on validated.

    Parameters
    ----------
    config:
        Broker settings. The subscription uses
        :meth:`~airheater.telemetry.config.MqttConfig.subscribe_pattern`, so one
        collector picks up every source publishing under the base topic.
    on_measurement:
        Called with each valid :class:`~airheater.telemetry.payload.Measurement`.
    on_status:
        Optional, called with ``(source, "online"|"offline")``.
    """

    def __init__(
        self,
        config: MqttConfig,
        on_measurement: Callable[[Measurement], None],
        on_status: Optional[Callable[[str, str], None]] = None,
        client_factory: Optional[Callable] = None,
    ) -> None:
        self.on_measurement = on_measurement
        self.on_status = on_status
        self.accepted = 0
        self.rejected = 0
        super().__init__(config, client_factory)
        self.client.on_message = self._on_message

    def _after_connect(self) -> None:
        # Re-subscribe on every connect: a reconnect starts a fresh session
        # unless the broker kept one, and a collector that is silently not
        # subscribed looks exactly like a quiet process.
        pattern = self.config.subscribe_pattern()
        self.client.subscribe(pattern, qos=self.config.qos)
        self.client.subscribe(self.config.topic + "/+/status", qos=1)
        log.info("subscribed to %s", pattern)

    def _on_message(self, client, userdata, message):
        self.handle_message(message.topic, message.payload)

    def handle_message(self, topic: str, payload: bytes) -> Optional[Measurement]:
        """Validate one message and dispatch it. No network involved.

        Returns the measurement when the payload was accepted, otherwise None.
        Invalid payloads are counted and logged, never raised: one malformed
        or hostile message must not stop the collector.
        """
        parts = topic.split("/")
        if parts[-1] == "status":
            source = parts[-2] if len(parts) >= 2 else "?"
            self._handle_status(source, payload)
            return None

        # The source in the topic is checked against the one in the body.
        topic_source = parts[-1] if parts else None
        try:
            measurement = decode_measurement(payload, source=topic_source)
        except InvalidPayload as exc:
            self.rejected += 1
            log.warning("rejected message on %r: %s", topic, exc)
            return None

        self.accepted += 1
        try:
            self.on_measurement(measurement)
        except Exception as exc:
            # A storage failure must not kill the subscriber thread.
            log.error("handler failed for sample k=%s: %r", measurement.k, exc)
        return measurement

    def _handle_status(self, source: str, payload: bytes) -> None:
        status = bytes(payload).decode("utf-8", errors="replace")[:16]
        if status not in ("online", "offline"):
            log.warning("ignoring unexpected status %r from %r", status, source)
            return
        log.info("source %s is %s", source, status)
        if self.on_status is not None:
            try:
                self.on_status(source, status)
            except Exception as exc:
                log.error("status handler failed: %r", exc)
