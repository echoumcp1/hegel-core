from hypothesis import given, strategies as st, settings
import socket
from threading import Thread
from hegel.protocol import write_packet, read_packet, Packet, Connection
from hegel.hegeld import run_server_on_connection
from hegel.sdk import Client

@settings(max_examples=1000)
@given(st.builds(
	Packet,
	message_id=st.integers(0, 1 << 31 - 1),
	channel=st.integers(0, 1 << 32 - 1),
    is_reply=st.booleans(),
    payload=st.binary(),
))
def test_roundtrip_packets(packet):

	reader, writer = socket.socketpair()
	try:
		write_packet(writer, packet)
		roundtripped = read_packet(reader)

		assert roundtripped == packet
	finally:
		reader.close()
		writer.close()


def test_basic_connection_can_negotiate_version_without_error():
	server_socket, client_socket = socket.socketpair()
	thread = Thread(
		target=run_server_on_connection, args=(Connection(server_socket, name="Server"),),
		daemon=True,
	)
	try:
		thread.start()
		client_connection = Connection(client_socket, name="Client")
		client = Client(client_connection)
	finally:
		client_connection.close()

	thread.join(timeout=1)


def test_request_handling():
	def add_server(connection):
		handler_channel = connection.connect_channel(1)
		@handler_channel.handle_requests
		def _(message):
			x, y = message
			return x + y

	server_socket, client_socket = socket.socketpair()
	thread = Thread(
		target=add_server, args=(Connection(server_socket, name="Server"),),
		daemon=True,
	)
	try:
		thread.start()
		client_connection = Connection(client_socket, name="Client")

		send_channel = client_connection.connect_channel(1)
		assert send_channel.request([2, 3]).get() == 5
	finally:
		client_connection.close()