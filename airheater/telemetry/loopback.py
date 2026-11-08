"""An in-process stand-in for an MQTT broker.

Routes published messages straight to the subscribers in the same process, so
the publisher, the payload validation and the storage layer can be exercised
end to end without installing a broker. Same idea as
:class:`~airheater.interfaces.simulated.SimulatedIO` on the process side: a
stand-in that lets you build and test everything around it first.

    broker = LoopbackBroker()
    sub = MqttSubscriber(config, on_measurement=store.store,
                         client_factory=broker.client_factory)
    pub = MqttPublisher(config, client_factory=broker.client_factory)

**Not a broker.** There is no network, no persistence, no QoS handling, no
session state and no authentication. It delivers synchronously on the
publishing thread. Use it for tests and demos; use a real broker for anything
that has to cross a machine boundary.
"""

from __future__ import annotations

from typing import List

__all__ = ["LoopbackBroker", "LoopbackClient"]


class LoopbackBroker:
    """Delivers messages between clients created by :meth:`client_factory`."""

    def __init__(self) -> None:
        self.clients: List["LoopbackClient"] = []
        self.retained = {}
        self.delivered = 0

    def client_factory(self, config) -> "LoopbackClient":
        """Pass this as ``client_factory`` to a publisher or subscriber."""
        client = LoopbackClient(self)
        self.clients.append(client)
        return client

    def route(self, topic: str, payload: bytes, retain: bool = False) -> None:
        if retain:
            self.retained[topic] = payload
        for client in self.clients:
            if client.matches(topic):
                self.delivered += 1
                client.deliver(topic, payload)


class LoopbackClient:
    """The subset of the paho client API that the endpoints actually use."""

    def __init__(self, broker: LoopbackBroker) -> None:
        self.broker = broker
        self.subscriptions: List[str] = []
        self.will = None
        self.published: List[tuple] = []
        self.host = None
        self.port = None
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    # -- the paho surface ------------------------------------------------

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, retain)

    def connect_async(self, host, port, keepalive=60):
        self.host, self.port = host, port

    connect = connect_async

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.reconnect_delays = (min_delay, max_delay)

    def loop_start(self):
        if self.on_connect is not None:
            self.on_connect(self, None, {}, _Success(), None)

    def loop_stop(self):
        pass

    def disconnect(self):
        if self.on_disconnect is not None:
            self.on_disconnect(self, None, {}, _Success(), None)

    def subscribe(self, pattern, qos=0):
        self.subscriptions.append(pattern)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))
        self.broker.route(topic, payload, retain)
        return _PublishInfo()

    # -- loopback only ---------------------------------------------------

    def matches(self, topic: str) -> bool:
        """Topic filter matching, single-level ``+`` wildcard only."""
        parts = topic.split("/")
        for pattern in self.subscriptions:
            pp = pattern.split("/")
            if len(pp) != len(parts):
                continue
            if all(p == "+" or p == a for p, a in zip(pp, parts)):
                return True
        return False

    def deliver(self, topic: str, payload: bytes) -> None:
        if self.on_message is not None:
            self.on_message(self, None, _Message(topic, payload))


class _Success:
    is_failure = False

    def __str__(self) -> str:
        return "Success"


class _PublishInfo:
    rc = 0
    mid = 1


class _Message:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload
