"""Broadcaster tests — fake opener callables only, no real sockets."""

import hashlib
import hmac
import json
import threading
import time
from types import SimpleNamespace

from orb.broadcast import Broadcaster, sign

SECRET = b"test-secret"
URL = "http://127.0.0.1:9/events"  # never contacted: opener is always injected


def wait_until(cond, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return cond()


class RecordingOpener:
    """Always-succeeds opener that captures requests and decoded bodies."""

    def __init__(self, status=200):
        self.status = status
        self.requests = []
        self.bodies = []
        self._lock = threading.Lock()

    def __call__(self, req, timeout=None):
        with self._lock:
            self.requests.append(req)
            self.bodies.append(json.loads(req.data.decode("utf-8")))
        return SimpleNamespace(status=self.status)


class FailFirstNOpener:
    """Raises for the first ``n`` calls, then records successes."""

    def __init__(self, n):
        self.n = n
        self.calls = 0
        self.bodies = []
        self._lock = threading.Lock()

    def __call__(self, req, timeout=None):
        with self._lock:
            self.calls += 1
            if self.calls <= self.n:
                raise OSError("connection refused")
            self.bodies.append(json.loads(req.data.decode("utf-8")))
        return SimpleNamespace(status=200)


class BlockingOpener:
    """Blocks every call on ``release``; sets ``entered`` on first call."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.bodies = []
        self._lock = threading.Lock()

    def __call__(self, req, timeout=None):
        self.entered.set()
        self.release.wait()
        with self._lock:
            self.bodies.append(json.loads(req.data.decode("utf-8")))
        return SimpleNamespace(status=200)


def make(opener, tmp_path, **kw):
    kw.setdefault("spool_path", str(tmp_path / "spool.jsonl"))
    return Broadcaster(URL, SECRET, opener=opener, **kw)


def test_signature_matches_reference_vector():
    secret, ts, body = b"test-secret", "1700000000", b'{"a":1}'
    expected = hmac.new(secret, b"1700000000." + body, hashlib.sha256).hexdigest()
    assert sign(secret, ts, body) == expected
    # pinned digest: the wire contract with the leader node must never drift
    assert expected == ("8cb2c3355fca388e9ac2caec004f4d5d"
                        "7045d74937ab5faad61dc11682247a9f")


def test_publish_returns_immediately_when_opener_blocks(tmp_path):
    opener = BlockingOpener()
    b = make(opener, tmp_path)
    b.publish({"n": 1})
    assert opener.entered.wait(5.0)  # worker is now stuck inside the opener
    t0 = time.perf_counter()
    b.publish({"n": 2})
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05
    opener.release.set()
    b.close()


def test_failure_spools_to_disk(tmp_path):
    def opener(req, timeout=None):
        raise OSError("network down")

    spool = tmp_path / "spool.jsonl"
    b = make(opener, tmp_path)
    payload = {"action": "open", "ticket": 111}
    b.publish(payload)
    assert wait_until(lambda: spool.exists() and spool.read_text().strip())
    lines = spool.read_text(encoding="utf-8").splitlines()
    assert [json.loads(ln) for ln in lines] == [payload]
    b.close()


def test_non_2xx_spools_to_disk(tmp_path):
    opener = RecordingOpener(status=500)
    spool = tmp_path / "spool.jsonl"
    b = make(opener, tmp_path)
    payload = {"action": "close", "ticket": 7}
    b.publish(payload)
    assert wait_until(lambda: spool.exists() and spool.read_text().strip())
    assert json.loads(spool.read_text(encoding="utf-8").splitlines()[0]) == payload
    b.close()


def test_spool_drained_in_order_on_recovery(tmp_path):
    opener = FailFirstNOpener(2)
    spool = tmp_path / "spool.jsonl"
    b = make(opener, tmp_path)
    e1, e2, e3 = {"seq": 1}, {"seq": 2}, {"seq": 3}
    b.publish(e1)
    b.publish(e2)
    b.publish(e3)
    assert wait_until(lambda: len(opener.bodies) == 3)
    assert opener.bodies == [e1, e2, e3]  # receive order == publish order
    b.close()
    assert not spool.exists() or not spool.read_text().strip()


def test_queue_full_drops_oldest_never_blocks(tmp_path):
    opener = BlockingOpener()
    b = make(opener, tmp_path, max_queue=3)
    b.publish({"seq": 0})
    assert opener.entered.wait(5.0)  # worker stuck on seq 0; queue is empty
    b.publish({"seq": 1})
    b.publish({"seq": 2})
    b.publish({"seq": 3})  # queue now full
    t0 = time.perf_counter()
    b.publish({"seq": 4})  # Full -> drop oldest (seq 1)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05
    assert b.dropped == 1
    opener.release.set()
    assert wait_until(lambda: len(opener.bodies) == 4)
    assert opener.bodies == [{"seq": 0}, {"seq": 2}, {"seq": 3}, {"seq": 4}]
    b.close()


def test_close_flushes_pending(tmp_path):
    opener = RecordingOpener()
    b = make(opener, tmp_path)
    payloads = [{"seq": i} for i in range(5)]
    for p in payloads:
        b.publish(p)
    b.close(drain_sec=5.0)
    assert opener.bodies == payloads


def test_close_never_hangs_when_opener_blocked(tmp_path):
    opener = BlockingOpener()
    b = make(opener, tmp_path)
    b.publish({"seq": 0})
    assert opener.entered.wait(5.0)
    t0 = time.perf_counter()
    b.close(drain_sec=0.2)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0  # bounded join, never hangs
    opener.release.set()  # let the daemon thread finish


def test_posted_body_is_exact_payload_json_with_signed_headers(tmp_path):
    opener = RecordingOpener()
    b = make(opener, tmp_path, now_fn=lambda: 1700000000.7)
    payload = {"schema_version": 1, "action": "open", "ticket": 111,
               "volume": 0.04, "sl": 4187.9, "tp": 0.0, "pnl": None,
               "source": {"strategy": "orb", "magic": 20260610}}
    b.publish(payload)
    assert wait_until(lambda: len(opener.requests) == 1)
    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert json.loads(req.data.decode("utf-8")) == payload
    headers = {k.lower(): v for k, v in req.headers.items()}
    assert headers["content-type"] == "application/json"
    assert headers["x-timestamp"] == "1700000000"
    assert headers["x-signature"] == sign(SECRET, "1700000000", req.data)
    b.close()
