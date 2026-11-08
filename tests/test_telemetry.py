"""Verification of the MQTT telemetry path and the database application.

Three things are checked here:

  * the payload validator rejects everything it should, since it is the
    trust boundary between the network and the database;
  * the storage layer cannot be injected into, demonstrated by attacking it;
  * the whole publish -> validate -> store chain works end to end.

The end-to-end test uses a fake in-process broker rather than a real one, so
it runs anywhere. The code under test is the same: the message handling is
separate from the transport, so ``handle_message`` is exercised exactly as
paho would call it.

Runs under pytest, or standalone:

    python tests/test_telemetry.py
"""

# Make the airheater package importable when this script is run directly,
# from any working directory, without installing it first.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import math
import os
import sqlite3
import tempfile

from airheater import AirHeaterSim, PIController, SimulatedIO, run_control_loop
from airheater.storage import SqliteStore
from airheater.telemetry import MqttConfig
from airheater.telemetry.loopback import LoopbackBroker
from airheater.telemetry.payload import (
    MAX_PAYLOAD_BYTES,
    SCHEMA_VERSION,
    InvalidPayload,
    decode_measurement,
    encode_sample,
)


# -- helpers --------------------------------------------------------------


def make_samples(n=20, Ts=0.1):
    """Run the control loop briefly and return its samples."""
    io = SimulatedIO(AirHeaterSim(Ts, T_init=20.0))
    pi = PIController(Kp=0.52, Ti=18.0, Ts=Ts)
    return run_control_loop(io, pi, 28.0, Ts, duration=n * Ts, realtime=False)


def valid_body(**overrides):
    body = {
        "schema": SCHEMA_VERSION,
        "source": "rig-1",
        "k": 3,
        "t": 0.3,
        "setpoint": 28.0,
        "measured": 21.4,
        "filtered": 21.2,
        "error": 6.8,
        "u": 1.23,
        "saturated": False,
        "jitter": 0.001,
    }
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


def expect_invalid(payload, note, **kwargs):
    try:
        decode_measurement(payload, **kwargs)
    except InvalidPayload:
        return
    raise AssertionError("should have been rejected: " + note)


# -- payload: the happy path ---------------------------------------------


def test_encode_decode_roundtrip():
    """Values survive the round trip to the documented wire precision.

    Sample.as_dict() rounds before encoding - temperatures to 3 decimals,
    the control signal to 4 - so this is not an exact round trip. See
    test_wire_precision_is_what_is_documented.
    """
    sample = make_samples(5)[-1]
    payload = encode_sample(sample, "rig-1")
    m = decode_measurement(payload)
    assert m.source == "rig-1"
    assert m.k == sample.k
    assert m.saturated == sample.saturated
    assert abs(m.u - sample.u) <= 5e-5           # u is rounded to 4 decimals
    assert abs(m.filtered - sample.filtered) <= 5e-4
    assert abs(m.measured - sample.measured) <= 5e-4
    assert abs(m.setpoint - sample.setpoint) <= 5e-4


def test_wire_precision_is_what_is_documented():
    """The rounding is a deliberate choice, so pin it down.

    0.001 degC on temperatures and 0.1 mV on the control signal are far below
    anything the rig can resolve, and they keep the payload small. If someone
    later needs full precision, this test is where the decision is recorded.
    """
    for sample in make_samples(40):
        body = json.loads(encode_sample(sample, "rig-1"))
        assert abs(body["measured"] - sample.measured) <= 5e-4
        assert abs(body["u"] - sample.u) <= 5e-5
        # No value should carry more digits than that.
        assert len(str(body["u"]).split(".")[-1]) <= 4


def test_encoded_payload_is_compact():
    sample = make_samples(5)[-1]
    assert len(encode_sample(sample, "rig-1")) < 400


def test_topic_source_must_match_body():
    payload = valid_body(source="rig-1")
    assert decode_measurement(payload, source="rig-1").source == "rig-1"
    # A client publishing on its own topic must not be able to claim it is
    # another rig.
    expect_invalid(payload, "source mismatch", source="rig-2")


# -- payload: what must be rejected --------------------------------------


def test_rejects_oversized_payload():
    expect_invalid(b"x" * (MAX_PAYLOAD_BYTES + 1), "oversized payload")


def test_rejects_non_json():
    expect_invalid(b"not json at all", "not JSON")
    expect_invalid(b"", "empty payload")
    expect_invalid(b"\xff\xfe\x00", "invalid UTF-8")


def test_rejects_non_object_json():
    expect_invalid(b"[1, 2, 3]", "JSON array")
    expect_invalid(b'"a string"', "JSON string")
    expect_invalid(b"42", "JSON number")


def test_rejects_missing_fields():
    for field in ("k", "t", "setpoint", "measured", "filtered", "u"):
        body = json.loads(valid_body())
        del body[field]
        expect_invalid(json.dumps(body).encode(), "missing " + field)


def test_rejects_missing_source():
    body = json.loads(valid_body())
    del body["source"]
    expect_invalid(json.dumps(body).encode(), "missing source")


def test_rejects_nan_and_infinity():
    """json.loads accepts these by default; they must not reach the database.

    A single NaN stored in the table poisons every AVG, MIN and MAX computed
    over it afterwards.
    """
    for literal in ("NaN", "Infinity", "-Infinity"):
        payload = valid_body().decode().replace("21.4", literal).encode()
        expect_invalid(payload, "measured = " + literal)


def test_rejects_out_of_range_values():
    expect_invalid(valid_body(measured=1e9), "absurd temperature")
    expect_invalid(valid_body(measured=-273.0), "below absolute zero")
    expect_invalid(valid_body(u=1e6), "absurd control signal")
    expect_invalid(valid_body(t=-1.0), "negative time")


def test_rejects_wrong_types():
    expect_invalid(valid_body(k="3"), "k as string")
    expect_invalid(valid_body(k=-1), "negative k")
    expect_invalid(valid_body(k=True), "k as bool")
    expect_invalid(valid_body(measured="21.4"), "measured as string")
    expect_invalid(valid_body(measured=True), "measured as bool")
    expect_invalid(valid_body(saturated="yes"), "saturated as string")
    expect_invalid(valid_body(measured=[1]), "measured as list")


def test_rejects_hostile_source_names():
    for bad, note in [
        ("'; DROP TABLE measurements;--", "SQL in the source name"),
        ("../../etc/passwd", "path traversal"),
        ("<script>alert(1)</script>", "HTML/JS"),
        ("rig 1", "whitespace"),
        ("", "empty"),
        ("x" * 65, "too long"),
        ("_leading", "does not start alphanumeric"),
    ]:
        expect_invalid(valid_body(source=bad), note)


def test_rejects_unknown_schema_version():
    expect_invalid(valid_body(schema=999), "future schema")
    expect_invalid(valid_body(schema="1"), "schema as string")


def test_encode_rejects_bad_source():
    sample = make_samples(2)[-1]
    try:
        encode_sample(sample, "bad source!")
    except InvalidPayload:
        return
    raise AssertionError("publisher should reject an invalid source name")


# -- storage --------------------------------------------------------------


def test_store_and_read_back():
    with SqliteStore(":memory:", commit_every=1) as store:
        for sample in make_samples(10):
            assert store.store(decode_measurement(encode_sample(sample, "rig-1")))
        assert store.count() == 10
        assert store.count("rig-1") == 10
        assert store.count("rig-2") == 0
        rows = store.recent("rig-1", limit=3)
        assert len(rows) == 3
        assert rows[0]["k"] > rows[-1]["k"]      # newest first


def test_duplicate_samples_are_not_stored_twice():
    """QoS 1 delivers at least once, so redelivery must be harmless."""
    with SqliteStore(":memory:", commit_every=1) as store:
        m = decode_measurement(encode_sample(make_samples(3)[-1], "rig-1"))
        assert store.store(m) is True
        assert store.store(m) is False       # same (source, k)
        assert store.count() == 1


def test_same_k_from_different_sources_both_stored():
    with SqliteStore(":memory:", commit_every=1) as store:
        sample = make_samples(3)[-1]
        assert store.store(decode_measurement(encode_sample(sample, "rig-1")))
        assert store.store(decode_measurement(encode_sample(sample, "rig-2")))
        assert store.count() == 2


def test_sql_injection_is_not_possible():
    """Attack the store with SQL in every value that reaches a statement.

    The classic payload closes the string literal, ends the statement and
    starts a new one. It works against code that formats values into SQL. It
    cannot work here, because every value is bound: the database receives the
    statement and the data separately, so the text is only ever a string.
    """
    attacks = [
        "'; DROP TABLE measurements;--",
        "' OR '1'='1",
        "x'); DELETE FROM measurements WHERE ('1'='1",
        '" OR 1=1--',
    ]
    with SqliteStore(":memory:", commit_every=1) as store:
        for sample in make_samples(5):
            store.store(decode_measurement(encode_sample(sample, "rig-1")))
        before = store.count()
        assert before == 5

        for attack in attacks:
            # Straight at the query methods, bypassing the payload validator -
            # so this tests the storage layer itself, not just the fact that
            # the validator would have rejected the name first.
            assert store.recent(attack) == []
            assert store.count(attack) == 0

        # The table is still there with every row intact.
        assert store.count() == before
        assert len(store.recent(limit=10)) == before


def test_source_validator_also_blocks_injection_earlier():
    """Defence in depth: the payload validator rejects it before storage."""
    expect_invalid(valid_body(source="'; DROP TABLE measurements;--"), "SQL source")


def test_store_survives_a_restart():
    path = os.path.join(tempfile.mkdtemp(), "airheater_test.db")
    try:
        with SqliteStore(path, commit_every=1) as store:
            for sample in make_samples(5):
                store.store(decode_measurement(encode_sample(sample, "rig-1")))
        with SqliteStore(path) as store:
            assert store.count() == 5
            assert store.sources() == ["rig-1"]
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_uncommitted_rows_are_flushed_on_close():
    path = os.path.join(tempfile.mkdtemp(), "airheater_flush.db")
    try:
        # commit_every well above the row count, so nothing commits on its own.
        with SqliteStore(path, commit_every=1000) as store:
            for sample in make_samples(5):
                store.store(decode_measurement(encode_sample(sample, "rig-1")))
        with SqliteStore(path) as store:
            assert store.count() == 5
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_closed_store_raises_cleanly():
    store = SqliteStore(":memory:")
    store.close()
    try:
        store.count()
    except RuntimeError:
        return
    raise AssertionError("a closed store should raise RuntimeError")


# -- configuration --------------------------------------------------------


def test_tls_is_the_default():
    config = MqttConfig(host="broker.example.com")
    assert config.tls is True
    assert config.port == 8883
    assert MqttConfig(host="h", tls=False).port == 1883


def test_password_over_plaintext_is_refused():
    """MQTT sends credentials in the CONNECT packet in the clear."""
    try:
        MqttConfig(host="h", tls=False, password="hunter2")
    except ValueError:
        pass
    else:
        raise AssertionError("password without TLS should be refused")
    # Allowed only when asked for explicitly.
    config = MqttConfig(host="h", tls=False, password="hunter2",
                        allow_plaintext_auth=True)
    assert config.password == "hunter2"


def test_config_warns_about_weak_settings():
    warnings = MqttConfig(host="h", tls=False).warnings()
    assert any("TLS is OFF" in w for w in warnings)
    assert any("credentials" in w for w in warnings)
    assert MqttConfig(host="h", username="u", password="p",
                      ca_certs="ca.pem").warnings() == []


def test_password_is_not_in_the_repr():
    """A config in a log line or a traceback must not leak the password."""
    config = MqttConfig(host="h", username="u", password="hunter2")
    assert "hunter2" not in repr(config)


def test_client_ids_are_unique():
    """Two clients sharing an id disconnect each other in a loop."""
    ids = {MqttConfig(host="h").client_id for _ in range(20)}
    assert len(ids) == 20


def test_topics_are_derived_consistently():
    config = MqttConfig(host="h", topic="airheater/data", source="rig-1")
    assert config.data_topic == "airheater/data/rig-1"
    assert config.status_topic == "airheater/data/rig-1/status"
    assert config.subscribe_pattern() == "airheater/data/+"


def test_wildcards_in_topic_are_refused():
    for bad in ("airheater/#", "airheater/+/data", "/airheater", ""):
        try:
            MqttConfig(host="h", topic=bad)
        except ValueError:
            continue
        raise AssertionError("topic should be refused: " + repr(bad))


def test_config_from_env(monkeypatch=None):
    keys = {
        "AIRHEATER_MQTT_HOST": "broker.test",
        "AIRHEATER_MQTT_TLS": "0",
        "AIRHEATER_MQTT_SOURCE": "rig-9",
        "AIRHEATER_MQTT_QOS": "2",
    }
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ.update(keys)
        config = MqttConfig.from_env()
        assert config.host == "broker.test"
        assert config.tls is False
        assert config.port == 1883
        assert config.source == "rig-9"
        assert config.qos == 2
        # An explicit override wins over the environment.
        assert MqttConfig.from_env(host="other.test").host == "other.test"
        # A None override does not wipe out the environment value.
        assert MqttConfig.from_env(host=None).host == "broker.test"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# -- end to end, with a fake broker --------------------------------------


# The loopback broker lives in the package, because it is useful outside the
# tests too - demos/mqtt_pipeline.py uses the same one.
FakeBroker = LoopbackBroker


def test_end_to_end_publish_validate_store():
    """Control loop -> publisher -> broker -> subscriber -> database."""
    from airheater.telemetry.mqtt import MqttPublisher, MqttSubscriber

    broker = FakeBroker()
    config = MqttConfig(host="fake", tls=False, source="rig-1")

    with SqliteStore(":memory:", commit_every=1) as store:
        subscriber = MqttSubscriber(
            config, on_measurement=store.store,
            client_factory=broker.client_factory,
        )
        subscriber.connect(timeout=0.1)

        publisher = MqttPublisher(
            config, every=1, client_factory=broker.client_factory
        )
        publisher.connect(timeout=0.1)

        samples = make_samples(25)
        for sample in samples:
            publisher.publish(sample)

        assert publisher.published == len(samples)
        assert subscriber.accepted == len(samples)
        assert subscriber.rejected == 0
        assert store.count("rig-1") == len(samples)

        # The stored values match what the loop actually produced, to the
        # wire precision.
        latest = store.recent("rig-1", limit=1)[0]
        assert latest["k"] == samples[-1].k
        assert abs(latest["u"] - samples[-1].u) <= 5e-5
        assert abs(latest["filtered"] - samples[-1].filtered) <= 5e-4
        assert latest["source"] == "rig-1"

        publisher.close()
        subscriber.close()


def test_publisher_decimates():
    from airheater.telemetry.mqtt import MqttPublisher

    broker = FakeBroker()
    config = MqttConfig(host="fake", tls=False, source="rig-1")
    publisher = MqttPublisher(config, every=10, client_factory=broker.client_factory)
    publisher.connect(timeout=0.1)
    for sample in make_samples(100):
        publisher.publish(sample)
    assert publisher.published == 10


def test_subscriber_drops_bad_messages_and_keeps_going():
    """One hostile message must not stop the collector."""
    from airheater.telemetry.mqtt import MqttPublisher, MqttSubscriber

    broker = FakeBroker()
    config = MqttConfig(host="fake", tls=False, source="rig-1")

    with SqliteStore(":memory:", commit_every=1) as store:
        subscriber = MqttSubscriber(
            config, on_measurement=store.store,
            client_factory=broker.client_factory,
        )
        subscriber.connect(timeout=0.1)

        topic = config.data_topic
        subscriber.handle_message(topic, b"garbage")
        subscriber.handle_message(topic, valid_body(source="rig-1", measured=1e9))
        subscriber.handle_message(topic, b"x" * (MAX_PAYLOAD_BYTES + 1))
        assert subscriber.rejected == 3
        assert store.count() == 0

        # Still working afterwards.
        publisher = MqttPublisher(config, client_factory=broker.client_factory)
        publisher.connect(timeout=0.1)
        publisher.publish(make_samples(3)[-1])
        assert subscriber.accepted == 1
        assert store.count() == 1


def test_subscriber_survives_a_failing_store():
    """A database outage must not kill the subscriber thread."""
    from airheater.telemetry.mqtt import MqttSubscriber

    broker = FakeBroker()
    config = MqttConfig(host="fake", tls=False, source="rig-1")

    def exploding_store(measurement):
        raise sqlite3.OperationalError("database is locked")

    subscriber = MqttSubscriber(
        config, on_measurement=exploding_store,
        client_factory=broker.client_factory,
    )
    subscriber.connect(timeout=0.1)
    m = subscriber.handle_message(config.data_topic, valid_body(source="rig-1"))
    assert m is not None          # it was accepted; the handler is what failed
    assert subscriber.accepted == 1


def test_status_messages_are_handled():
    from airheater.telemetry.mqtt import MqttSubscriber

    broker = FakeBroker()
    config = MqttConfig(host="fake", tls=False, source="rig-1")
    seen = []
    subscriber = MqttSubscriber(
        config, on_measurement=lambda m: None,
        on_status=lambda s, st: seen.append((s, st)),
        client_factory=broker.client_factory,
    )
    subscriber.connect(timeout=0.1)
    subscriber.handle_message(config.status_topic, b"online")
    subscriber.handle_message(config.status_topic, b"offline")
    subscriber.handle_message(config.status_topic, b"pwned")   # ignored
    assert seen == [("rig-1", "online"), ("rig-1", "offline")]


def test_last_will_is_registered_before_connecting():
    """The broker must know the offline message before the link can drop."""
    from airheater.telemetry.mqtt import MqttPublisher

    broker = FakeBroker()
    config = MqttConfig(host="fake", tls=False, source="rig-1")
    publisher = MqttPublisher(config, client_factory=broker.client_factory)
    assert publisher.client.will == (config.status_topic, b"offline", True)


def test_publisher_announces_online_and_offline():
    from airheater.telemetry.mqtt import MqttPublisher

    broker = FakeBroker()
    config = MqttConfig(host="fake", tls=False, source="rig-1")
    publisher = MqttPublisher(config, client_factory=broker.client_factory)
    publisher.connect(timeout=0.1)
    assert (config.status_topic, b"online") in publisher.client.published
    publisher.close()
    assert (config.status_topic, b"offline") in publisher.client.published


def test_publish_failure_does_not_reach_the_control_loop():
    """Telemetry must never be able to stop the process."""
    from airheater.telemetry.mqtt import MqttPublisher

    broker = FakeBroker()
    config = MqttConfig(host="fake", tls=False, source="rig-1")
    publisher = MqttPublisher(config, client_factory=broker.client_factory)
    publisher.connect(timeout=0.1)

    def explode(*a, **kw):
        raise OSError("network is down")

    publisher.client.publish = explode

    io = SimulatedIO(AirHeaterSim(0.1, T_init=20.0))
    pi = PIController(Kp=0.52, Ti=18.0, Ts=0.1)
    # Long enough to actually reach setpoint: theta_t is 22 s, so a 30 s run
    # would still be on the way up and would prove nothing about control.
    samples = run_control_loop(io, pi, 28.0, 0.1, duration=300.0,
                               on_sample=publisher.publish, realtime=False)
    # The loop ran to completion and still did its job.
    assert len(samples) == 3000
    assert abs(samples[-1].filtered - 28.0) < 0.1, samples[-1].filtered
    # Every publish failed, and every failure was absorbed.
    assert publisher.dropped == 3000
    assert publisher.published == 0


def test_unreachable_broker_does_not_raise():
    """A dead broker must not stop the control application.

    Uses the real paho client against a port nothing is listening on. This is
    the case that paho's blocking connect() turns into a ConnectionRefusedError
    in the caller's thread; connect_async() hands it to the network loop
    instead, which retries.
    """
    from airheater.telemetry.mqtt import MqttPublisher

    # Port 1 on loopback: refused immediately, no waiting on a timeout.
    config = MqttConfig(host="127.0.0.1", port=1, tls=False, source="rig-1")
    publisher = MqttPublisher(config)
    try:
        assert publisher.connect(timeout=1.0) is False   # reported, not raised

        # And publishing while disconnected is absorbed, not raised.
        for sample in make_samples(5):
            publisher.publish(sample)
        assert publisher.published + publisher.dropped == 5
    finally:
        publisher.close()


def test_control_loop_runs_against_a_dead_broker():
    """The whole application path, with nothing listening on the other end."""
    from airheater.telemetry.mqtt import MqttPublisher

    config = MqttConfig(host="127.0.0.1", port=1, tls=False, source="rig-1")
    publisher = MqttPublisher(config, every=10)
    try:
        publisher.connect(timeout=0.5)
        io = SimulatedIO(AirHeaterSim(0.1, T_init=20.0))
        pi = PIController(Kp=0.52, Ti=18.0, Ts=0.1)
        samples = run_control_loop(io, pi, 28.0, 0.1, duration=300.0,
                                   on_sample=publisher.publish, realtime=False)
        assert len(samples) == 3000
        assert abs(samples[-1].filtered - 28.0) < 0.1, samples[-1].filtered
    finally:
        publisher.close()


def _run_standalone():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print("FAIL  {0}: {1}".format(name, exc))
        else:
            print("ok    {0}".format(name))
    print("\n{0} passed, {1} failed, {2} total".format(
        len(tests) - failures, failures, len(tests)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
