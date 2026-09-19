"""Bounded data-only messages across the research-worker trust boundary."""
import json

MAX_MESSAGE_BYTES = 1024 * 1024
KINDS = {'heartbeat', 'phase', 'progress', 'message', 'trial', 'result', 'finished',
         'sample', 'publish_artifact', 'publish_checkpoint'}


class Stream:
    """Inherited pipes avoid opening a network listener across a sandbox boundary."""
    def __init__(self, incoming, outgoing):
        self.incoming, self.outgoing = incoming, outgoing

    def send_bytes(self, payload):
        self.outgoing.write(payload + b'\n')
        self.outgoing.flush()

    def recv_bytes(self, maximum):
        payload = self.incoming.readline(maximum + 2)
        if not payload:
            raise EOFError('Evidence pipe closed')
        if len(payload) > maximum + 1 or not payload.endswith(b'\n'):
            raise ValueError('Evidence pipe frame exceeds limit or is incomplete')
        return payload[:-1]

    def close(self):
        self.incoming.close()
        self.outgoing.close()


def send(connection, value):
    payload = json.dumps(value, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError('Evidence message exceeds limit; publish large data as an artifact')
    connection.send_bytes(payload)


def receive(connection, worker_message=False):
    # Never Connection.recv(): it deserializes executable pickle objects.
    value = json.loads(connection.recv_bytes(MAX_MESSAGE_BYTES).decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected a data object')
    if worker_message and (set(value) != {'kind', 'data'} or value['kind'] not in KINDS
                           or not isinstance(value['data'], dict)):
        raise ValueError('Worker message is outside its granted evidence interface')
    return value
