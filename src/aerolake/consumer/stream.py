"""ZeroMQ Pub/Sub streaming of capture frames (ADR-008).

This is the network "delivery" layer of the pipeline: it takes the frames a
:class:`~aerolake.consumer.player.CapturePlayer` emits (paced at the recorded
cadence) and **publishes them on a ZeroMQ PUB socket**, so any number of
subscribers can consume them live. It's the long-promised
``Consumer → ZeroMQ Pub/Sub`` step from the original architecture, deferred by
ADR-002/004 and now built on top of the player.

Wire format (one ZeroMQ multipart message per frame)
----------------------------------------------------
Three parts, so a SUB socket can filter cheaply on the first one:

  1. ``topic``   — UTF-8 bytes (e.g. the signal type). SUB sockets subscribe to
                   a topic *prefix*, so this is the routing key.
  2. ``header``  — JSON bytes: ``{"index", "n", "dtype"}`` describing the frame.
  3. ``payload`` — the raw IQ sample bytes (``ndarray.tobytes()``).

``encode_frame`` / ``decode_frame`` are **pure** (no sockets), so the format is
unit-tested without any networking. ``FramePublisher`` takes an *injectable*
socket, so its behaviour is testable with a fake socket too — no real PUB/SUB
round-trip (and its flaky "slow joiner" timing) is needed in tests.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import structlog
import zmq

logger = structlog.get_logger(__name__)


def encode_frame(
    topic: str, header: dict[str, Any], samples: np.ndarray
) -> list[bytes]:
    """Serialise one frame into a 3-part ZeroMQ multipart message."""
    return [
        topic.encode("utf-8"),
        json.dumps(header).encode("utf-8"),
        samples.tobytes(),
    ]


def decode_frame(parts: list[bytes]) -> tuple[str, dict[str, Any], np.ndarray]:
    """Inverse of :func:`encode_frame`.

    Reconstructs ``(topic, header, samples)`` from the multipart message,
    rebuilding the numpy array using the dtype recorded in the header.
    """
    topic_b, header_b, payload = parts
    header = json.loads(header_b.decode("utf-8"))
    samples = np.frombuffer(payload, dtype=np.dtype(header["dtype"]))
    return topic_b.decode("utf-8"), header, samples


class FramePublisher:
    """Publishes capture frames on a ZeroMQ PUB socket.

    ``publish`` has the exact ``(index, frame)`` signature of the player's
    ``on_frame`` hook, so you can wire them together directly:

        player.play(key, on_frame=publisher.publish)

    The capture's ``center_freq`` and ``sample_rate`` are fixed for the whole
    stream, so they're set once at construction and echoed in every frame
    header. A subscriber (e.g. a positioning algorithm) therefore knows the
    radio context from the very first frame it receives, without a separate
    handshake -- and even if it joins mid-stream (ZeroMQ's "slow joiner").
    """

    def __init__(
        self,
        socket: zmq.Socket,
        topic: str,
        *,
        center_freq: float | None = None,
        sample_rate: float | None = None,
    ) -> None:
        self._socket = socket
        self._topic = topic
        self._center_freq = center_freq
        self._sample_rate = sample_rate

    @classmethod
    def bind(
        cls,
        address: str,
        topic: str,
        *,
        center_freq: float | None = None,
        sample_rate: float | None = None,
    ) -> FramePublisher:
        """Create a PUB socket bound to ``address`` (e.g. ``tcp://*:5555``)."""
        context = zmq.Context.instance()
        socket = context.socket(zmq.PUB)
        socket.bind(address)
        return cls(
            socket, topic, center_freq=center_freq, sample_rate=sample_rate
        )

    def publish(self, index: int, frame: np.ndarray) -> None:
        """Send one frame as a multipart message (matches on_frame signature)."""
        header: dict[str, Any] = {
            "index": index,
            "n": len(frame),
            "dtype": str(frame.dtype),
        }
        if self._center_freq is not None:
            header["center_freq"] = self._center_freq
        if self._sample_rate is not None:
            header["sample_rate"] = self._sample_rate
        self._socket.send_multipart(encode_frame(self._topic, header, frame))

    def close(self) -> None:
        """Close the underlying socket."""
        self._socket.close()


class FrameSubscriber:
    """Minimal subscriber: connects a SUB socket and yields decoded frames."""

    def __init__(self, socket: zmq.Socket) -> None:
        self._socket = socket

    @classmethod
    def connect(cls, address: str, topic: str = "") -> FrameSubscriber:
        """Connect a SUB socket to ``address`` and subscribe to ``topic``.

        An empty topic subscribes to everything. Note ZeroMQ's "slow joiner"
        property: a SUB that connects after the PUB has started may miss early
        messages — fine for a continuous stream, but worth knowing.
        """
        context = zmq.Context.instance()
        socket = context.socket(zmq.SUB)
        socket.connect(address)
        socket.setsockopt_string(zmq.SUBSCRIBE, topic)
        return cls(socket)

    def recv(self) -> tuple[str, dict[str, Any], np.ndarray]:
        """Block until the next frame arrives and return it decoded."""
        return decode_frame(self._socket.recv_multipart())

    def close(self) -> None:
        self._socket.close()
