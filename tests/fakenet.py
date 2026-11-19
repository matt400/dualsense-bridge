"""Shared fake socket for the plain-script tests: ``PacketSock(pkts)`` hands out queued datagrams to ``recv`` and records ``send``.

It stands in for the sender's connected UDP socket (``sender_net.Net.sock``): ``recv`` pops the next queued packet and raises
BlockingIOError once the queue is empty, the way a drained non-blocking socket does; ``send`` keeps every datagram in ``sent``.
"""


class PacketSock:
    """A non-blocking UDP socket stand-in: queued packets in, sent packets recorded."""

    def __init__(self, pkts=()):
        self.pkts, self.sent = list(pkts), []

    def recv(self, n):
        if not self.pkts:
            raise BlockingIOError
        return self.pkts.pop(0)

    def send(self, data):
        self.sent.append(data)
