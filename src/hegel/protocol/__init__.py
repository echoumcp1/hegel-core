"""
This defines the basic wire protocol of Hegel. It is a message oriented protocol,
with many logical connections (channels) multiplexed over a single connections.
Messages are in principle arbitrary bytes, and this is used for the version handshakes,
but in actual operation will be CBOR.

Handshake for running a test:

1. SDK creates a channel and sends a message to Hegel saying it
   wants a test of this name running on this channel.
2. Hegel creates a ConjectureRunner running in a thread whose test
   function operates as follows:
    1. Creates a channel and a ConjectureData and sends a message
       on the test channel saying it wants a new test case using
       this data.
    2. Goes into data serving mode where the Hegel SDK can now make
       requests for data to be generated (in our limited JSON
       schema) on that channel.
    3. stop/start_span, target, and mark_complete can also be sent.
       Note that the test function in the SDK *must* call
       mark complete.
3. Once the runner has finished running, it makes some number
   (possibly zero) of additional calls with test cases, adding an
   is_final: True to the args.
4. After that it sends a test_done message.
"""

from hegel.protocol.utils import ChannelId, MessageId, ProtocolError, RequestError

__all__ = ["ChannelId", "MessageId", "ProtocolError", "RequestError"]
