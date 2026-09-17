from redpitaya_acquisition.streaming.broadcaster import Broadcaster
from redpitaya_acquisition.streaming.client_queue import ClientQueue


def test_client_queue_drops_oldest() -> None:
    queue = ClientQueue(2)
    queue.put_latest(b"one")
    queue.put_latest(b"two")
    queue.put_latest(b"three")
    assert queue.dropped_messages == 1
    assert queue.queue.get_nowait() == b"two"
    assert queue.queue.get_nowait() == b"three"


def test_multiple_clients_share_same_bytes_object() -> None:
    broadcaster = Broadcaster(2)
    _, first = broadcaster.subscribe()
    _, second = broadcaster.subscribe()
    message = b"frame"
    broadcaster.publish(message)
    assert first.queue.get_nowait() is message
    assert second.queue.get_nowait() is message
