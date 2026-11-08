"""MQTT connection settings.

Credentials are read from the environment, never written in source. A password
in a file that ends up in version control is the most common way a lab system
leaks, and it is the easiest one to avoid.

    set AIRHEATER_MQTT_HOST=broker.example.com
    set AIRHEATER_MQTT_USERNAME=airheater
    set AIRHEATER_MQTT_PASSWORD=...
    set AIRHEATER_MQTT_TLS=1

On Linux or the Raspberry Pi use ``export`` instead of ``set``.

TLS is on by default. Turning it off is possible, because an isolated lab
network is a legitimate case, but it has to be done deliberately
(``AIRHEATER_MQTT_TLS=0``) and :meth:`MqttConfig.warnings` will say what you
gave up. Sending a password over an unencrypted connection is refused outright
unless ``allow_plaintext_auth`` is also set: MQTT sends credentials in the
CONNECT packet in the clear, so anyone on the path can read them.
"""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

__all__ = ["MqttConfig", "ENV_PREFIX"]

ENV_PREFIX = "AIRHEATER_MQTT_"

DEFAULT_PORT_PLAIN = 1883
DEFAULT_PORT_TLS = 8883


@dataclass
class MqttConfig:
    """Everything needed to reach a broker.

    Parameters
    ----------
    host, port:
        Broker address. ``port`` defaults to 8883 with TLS, 1883 without.
    topic:
        Base topic. Published data goes to ``<topic>/<source>``.
    source:
        Identifies this rig or control application.
    username, password:
        Broker credentials. Leave unset for an anonymous broker.
    tls:
        Encrypt the connection. On by default.
    ca_certs:
        Path to a CA bundle used to verify the broker certificate. Leave unset
        to use the system trust store, which is right for a broker with a
        certificate from a public CA. A self-signed lab broker needs its CA
        file here.
    certfile, keyfile:
        Client certificate, if the broker authenticates clients that way.
    tls_insecure:
        Skip hostname verification. This disables the check that stops an
        attacker presenting a valid certificate for a different host, so it
        turns TLS into encryption without authentication. Only for a lab
        broker whose certificate does not carry its hostname, and never in
        production.
    allow_plaintext_auth:
        Permit sending a password over an unencrypted connection. Off by
        default, and the configuration is rejected rather than silently
        downgraded.
    qos:
        0 = at most once (may lose samples), 1 = at least once (may duplicate,
        which the database handles with a unique key), 2 = exactly once.
        1 is the right default for process data.
    """

    host: str = "localhost"
    port: Optional[int] = None
    topic: str = "airheater/data"
    source: str = "airheater-1"
    client_id: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    tls: bool = True
    ca_certs: Optional[str] = None
    certfile: Optional[str] = None
    keyfile: Optional[str] = None
    tls_insecure: bool = False
    allow_plaintext_auth: bool = False
    qos: int = 1
    keepalive: int = 60

    def __post_init__(self) -> None:
        if self.port is None:
            self.port = DEFAULT_PORT_TLS if self.tls else DEFAULT_PORT_PLAIN
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("port out of range: {0}".format(self.port))
        if self.qos not in (0, 1, 2):
            raise ValueError("qos must be 0, 1 or 2, got {0!r}".format(self.qos))
        if not self.topic or self.topic.startswith("/") or "#" in self.topic or "+" in self.topic:
            raise ValueError(
                "topic must be a concrete path with no wildcards, got {0!r}".format(self.topic)
            )
        if self.client_id is None:
            # Two clients sharing a client id kick each other off the broker in
            # a reconnect loop, so make it unique per process.
            self.client_id = "{0}-{1}-{2}".format(
                self.source, socket.gethostname()[:20], uuid.uuid4().hex[:8]
            )
        if self.password and not self.tls and not self.allow_plaintext_auth:
            raise ValueError(
                "refusing to send a password over an unencrypted connection: MQTT "
                "puts credentials in the CONNECT packet in the clear. Enable TLS "
                "({0}TLS=1), or set {0}ALLOW_PLAINTEXT_AUTH=1 if this really is an "
                "isolated lab network.".format(ENV_PREFIX)
            )

    @property
    def data_topic(self) -> str:
        """Topic this source publishes its samples on."""
        return "{0}/{1}".format(self.topic, self.source)

    @property
    def status_topic(self) -> str:
        """Topic for the online/offline status, used as the last will."""
        return "{0}/{1}/status".format(self.topic, self.source)

    def subscribe_pattern(self) -> str:
        """Topic filter a collector uses to receive every source's data.

        Single-level wildcard, so it picks up ``<topic>/<source>`` but not the
        ``<topic>/<source>/status`` messages below it.
        """
        return "{0}/+".format(self.topic)

    def warnings(self) -> List[str]:
        """Security weaknesses in this configuration, in plain language.

        Printed at startup so a weak setup is visible rather than implied.
        """
        out = []
        if not self.tls:
            out.append(
                "TLS is OFF: process data and any credentials travel in clear "
                "text and can be read or modified by anyone on the network path. "
                "Acceptable only on an isolated lab network."
            )
        if self.tls and self.tls_insecure:
            out.append(
                "TLS hostname verification is OFF: the connection is encrypted "
                "but not authenticated, so it can be intercepted by anyone able "
                "to redirect the traffic."
            )
        if not self.username:
            out.append(
                "No broker credentials: the broker is being used anonymously, so "
                "any client that can reach it can publish forged measurements or "
                "read yours."
            )
        return out

    @classmethod
    def from_env(cls, prefix: str = ENV_PREFIX, **overrides) -> "MqttConfig":
        """Build a configuration from environment variables.

        Keyword arguments override the environment, which is what the command
        line flags use.
        """
        def env(name, default=None):
            return os.environ.get(prefix + name, default)

        def env_bool(name, default):
            raw = env(name)
            if raw is None:
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        def env_int(name, default):
            raw = env(name)
            return default if raw is None else int(raw)

        values = dict(
            host=env("HOST", "localhost"),
            port=env_int("PORT", None),
            topic=env("TOPIC", "airheater/data"),
            source=env("SOURCE", "airheater-1"),
            client_id=env("CLIENT_ID"),
            username=env("USERNAME"),
            password=env("PASSWORD"),
            tls=env_bool("TLS", True),
            ca_certs=env("CA_CERTS"),
            certfile=env("CERTFILE"),
            keyfile=env("KEYFILE"),
            tls_insecure=env_bool("TLS_INSECURE", False),
            allow_plaintext_auth=env_bool("ALLOW_PLAINTEXT_AUTH", False),
            qos=env_int("QOS", 1),
            keepalive=env_int("KEEPALIVE", 60),
        )
        # Drop overrides that were not actually given, so None does not wipe
        # out a value that came from the environment.
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)
