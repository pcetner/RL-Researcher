import pickle
import io
from multiprocessing import Pipe

import pytest

from rl_researcher import channel


def test_stream_framing_preserves_embedded_newlines_and_refuses_truncation():
    output = io.BytesIO()
    channel.send(channel.Stream(io.BytesIO(), output), {'text':'first\nsecond'})
    assert channel.receive(channel.Stream(io.BytesIO(output.getvalue()), io.BytesIO())) == {'text':'first\nsecond'}
    with pytest.raises(ValueError, match='incomplete'):
        channel.receive(channel.Stream(io.BytesIO(b'{"partial":1}'), io.BytesIO()))


def test_data_channel_rejects_executable_serialization():
    read, write = Pipe(duplex=False)
    try:
        write.send_bytes(pickle.dumps({'kind': 'heartbeat', 'data': {}}))
        with pytest.raises((ValueError, UnicodeError)):
            channel.receive(read, worker_message=True)
    finally:
        read.close()
        write.close()


def test_worker_cannot_publish_authoritative_state_events():
    read, write = Pipe(duplex=False)
    try:
        channel.send(write, {'kind': 'state', 'data': {'state': 'Completed'}})
        with pytest.raises(ValueError, match='granted evidence'):
            channel.receive(read, worker_message=True)
        channel.send(write, {'kind': 'progress', 'data': {'decision': 5}})
        assert channel.receive(read, worker_message=True)['data']['decision'] == 5
    finally:
        read.close()
        write.close()
