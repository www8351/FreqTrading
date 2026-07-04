"""Leader-node sidecar tests — real ThreadingHTTPServer on port 0, urllib client.

The HMAC wire contract is shared with :mod:`orb.broadcast` (``sign``); these
tests exercise the receiving end: signature verification, timestamp skew,
storage seq assignment, read endpoints, optional publisher fan-out, and the
COPYTRADE_SECRET startup guard.
"""

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

from leader.store import LeaderStore
from leader.server import serve
from orb.broadcast import sign

SECRET = b"test-secret"
NOW = 1700000000.0  # injected now_fn keeps every skew check deterministic


class FakePublisher:
    def __init__(self, fail=False):
        self.fail = fail
        self.published = []

    def publish(self, topic, payload):
        if self.fail:
            raise RuntimeError("zmq down")
        self.published.append((topic, payload))


@contextmanager
def running(tmp_path, publisher=None, now_fn=None, max_skew=300.0):
    store = LeaderStore(str(tmp_path / "events.jsonl"))
    server = serve(0, store, SECRET, publisher=publisher,
                   max_skew=max_skew, now_fn=now_fn or (lambda: NOW))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], store
    finally:
        server.shutdown()
        server.server_close()


def post_event(port, payload=None, ts=None, sig=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    ts = str(int(NOW)) if ts is None else ts
    sig = sign(SECRET, ts, body) if sig is None else sig
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/events", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Timestamp": ts, "X-Signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                    timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


# -- store ----------------------------------------------------------------

def test_store_append_assigns_seq_and_latest(tmp_path):
    store = LeaderStore(str(tmp_path / "ev.jsonl"))
    assert store.append({"a": 1}) == 1
    assert store.append({"a": 2}) == 2
    assert store.append({"a": 3}) == 3
    last2 = store.latest(2)
    assert [e["a"] for e in last2] == [2, 3]
    assert [e["seq"] for e in last2] == [2, 3]
    assert [e["seq"] for e in store.latest()] == [1, 2, 3]


def test_store_seq_resumes_across_reopen(tmp_path):
    path = str(tmp_path / "ev.jsonl")
    store = LeaderStore(path)
    store.append({"a": 1})
    store.append({"a": 2})
    reopened = LeaderStore(path)
    assert reopened.append({"a": 3}) == 3
    assert reopened.count() == 3


# -- POST /events ----------------------------------------------------------

def test_valid_signature_stores_and_returns_seq(tmp_path):
    with running(tmp_path) as (port, store):
        status, body = post_event(port, {"symbol": "XAUUSD", "action": "open"})
        assert status == 200
        assert body == {"ok": True, "seq": 1}
        status, body = post_event(port, {"symbol": "XAUUSD", "action": "close"})
        assert (status, body["seq"]) == (200, 2)
        events = store.latest()
        assert [e["action"] for e in events] == ["open", "close"]
        assert [e["seq"] for e in events] == [1, 2]


def test_bad_signature_rejected_nothing_stored(tmp_path):
    with running(tmp_path) as (port, store):
        status, _ = post_event(port, {"symbol": "XAUUSD"}, sig="deadbeef")
        assert status == 401
        assert store.latest() == []
        assert store.count() == 0


def test_stale_timestamp_rejected(tmp_path):
    with running(tmp_path) as (port, store):
        stale = str(int(NOW - 9999))
        status, _ = post_event(port, {"symbol": "XAUUSD"}, ts=stale)
        assert status == 408
        assert store.latest() == []


def test_timestamp_at_max_skew_accepted(tmp_path):
    with running(tmp_path) as (port, store):
        edge = str(int(NOW - 300))
        status, body = post_event(port, {"symbol": "XAUUSD"}, ts=edge)
        assert (status, body["seq"]) == (200, 1)


def test_malformed_json_rejected(tmp_path):
    with running(tmp_path) as (port, store):
        status, _ = post_event(port, raw=b"{not json")
        assert status == 400
        assert store.latest() == []


def test_non_numeric_timestamp_rejected(tmp_path):
    with running(tmp_path) as (port, store):
        status, _ = post_event(port, {"symbol": "XAUUSD"}, ts="yesterday")
        assert status == 400
        assert store.latest() == []


# -- GET endpoints ----------------------------------------------------------

def test_latest_returns_last_n(tmp_path):
    with running(tmp_path) as (port, _store):
        for i in range(5):
            post_event(port, {"symbol": "XAUUSD", "i": i})
        status, events = get(port, "/events/latest?n=2")
        assert status == 200
        assert [e["i"] for e in events] == [3, 4]
        assert [e["seq"] for e in events] == [4, 5]
        status, events = get(port, "/events/latest")  # default n=50
        assert (status, len(events)) == (200, 5)


def test_health_endpoint(tmp_path):
    with running(tmp_path) as (port, _store):
        status, body = get(port, "/health")
        assert (status, body) == (200, {"ok": True, "events": 0})
        post_event(port, {"symbol": "XAUUSD"})
        post_event(port, {"symbol": "XAUUSD"})
        status, body = get(port, "/health")
        assert (status, body) == (200, {"ok": True, "events": 2})


def test_unknown_path_404(tmp_path):
    with running(tmp_path) as (port, _store):
        assert get(port, "/nope")[0] == 404
        status, _ = post_event(port, {"symbol": "XAUUSD"})
        assert status == 200  # sanity: only unknown paths 404


# -- publisher fan-out -------------------------------------------------------

def test_publisher_called_on_ingest(tmp_path):
    pub = FakePublisher()
    with running(tmp_path, publisher=pub) as (port, _store):
        post_event(port, {"symbol": "XAUUSD", "action": "open"})
        assert len(pub.published) == 1
        topic, payload = pub.published[0]
        assert topic == "XAUUSD"
        assert payload["action"] == "open"
        assert payload["seq"] == 1  # ZMQ frame == store line (schema doc)


def test_publisher_failure_does_not_break_ingest(tmp_path):
    pub = FakePublisher(fail=True)
    with running(tmp_path, publisher=pub) as (port, store):
        status, body = post_event(port, {"symbol": "XAUUSD"})
        assert (status, body["seq"]) == (200, 1)
        assert store.count() == 1


# -- __main__ startup guard ---------------------------------------------------

def test_missing_secret_refuses_start(monkeypatch, capsys):
    from leader.__main__ import main
    monkeypatch.delenv("COPYTRADE_SECRET", raising=False)
    rc = main(["--port", "0"])
    assert rc == 2
    assert "COPYTRADE_SECRET" in capsys.readouterr().err


def test_empty_secret_refuses_start(monkeypatch, capsys):
    from leader.__main__ import main
    monkeypatch.setenv("COPYTRADE_SECRET", "")
    rc = main(["--port", "0"])
    assert rc == 2
    assert "COPYTRADE_SECRET" in capsys.readouterr().err
