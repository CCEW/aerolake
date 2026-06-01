"""Unit tests for aerolake.consumer.stream (ZeroMQ Pub/Sub frame streaming).

The wire format (encode/decode) and the publisher are tested deterministically
with pure functions and a fake socket. One integration test exercises a real
ZeroMQ PUB/SUB pair over inproc, using a publish-and-poll retry loop to sidestep
ZeroMQ's "slow joiner" timing.
"""

from __future__ import annotations

import numpy as np
import zmq

from aerolake.consumer.stream import (
    FramePublisher,
    FrameSubscriber,
    decode_frame,
    encode_frame,
)


def _frame(n: int = 8) -> np.ndarray:
    return (np.arange(n) + 1j * np.arange(n)).astype(np.complex64)


# --- Pure wire format ----------------------------------------------------

def test_encode_decode_roundtrip() -> None:
    samples = _frame()
    parts = encode_frame("gnss_l1", {"index": 3, "n": 8, "dtype": "complex64"}, samples)
    assert len(parts) == 3  # topic, header, payload
    topic, header, out = decode_frame(parts)
    assert topic == "gnss_l1"
    assert header["index"] == 3
    np.testing.assert_array_equal(out, samples)


# --- FramePublisher with a fake socket -----------------------------------

class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[list[bytes]] = []
        self.closed = False

    def send_multipart(self, parts: list[bytes]) -> None:
        self.sent.append(parts)

    def close(self) -> None:
        self.closed = True


def test_publisher_sends_well_formed_multipart() -> None:
    sock = _FakeSocket()
    pub = FramePublisher(sock, "gnss_l1")

    frame = _frame(4)
    pub.publish(2, frame)  # (index, frame) == the player's on_frame signature

    assert len(sock.sent) == 1
    topic, header, out = decode_frame(sock.sent[0])
    assert topic == "gnss_l1"
    assert header == {"index": 2, "n": 4, "dtype": "complex64"}
    np.testing.assert_array_equal(out, frame)


def test_publisher_close_closes_socket() -> None:
    sock = _FakeSocket()
    FramePublisher(sock, "t").close()
    assert sock.closed is True


# --- Real PUB/SUB over inproc -------------------------------------------

def test_real_pubsub_roundtrip() -> None:
    """A real SUB receives what a real FramePublisher (PUB) sends."""
    ctx = zmq.Context.instance()
    sub_sock = ctx.socket(zmq.SUB)
    sub_sock.connect("inproc://aerolake-test-stream")
    sub_sock.setsockopt_string(zmq.SUBSCRIBE, "")  # all topics
    pub_sock = ctx.socket(zmq.PUB)
    pub_sock.bind("inproc://aerolake-test-stream")

    publisher = FramePublisher(pub_sock, "gnss_l1")
    subscriber = FrameSubscriber(sub_sock)
    poller = zmq.Poller()
    poller.register(sub_sock, zmq.POLLIN)

    frame = _frame(4)
    received = None
    # PUB drops messages until the SUB subscription has propagated ("slow
    # joiner"), so publish repeatedly and poll until the first one lands.
    for _ in range(200):
        publisher.publish(0, frame)
        if dict(poller.poll(timeout=10)):
            received = subscriber.recv()
            break

    subscriber.close()
    publisher.close()

    assert received is not None, "no frame received over the PUB/SUB pair"
    topic, _header, out = received
    assert topic == "gnss_l1"
    np.testing.assert_array_equal(out, frame)
