from hegel.protocol import Connection, Channel, VERSION_NEGOTIATION_MESSAGE, VERSION_NEGOTIATION_OK


class Client:
	def __init__(self, connection: Connection):
		id = connection.control_channel.send_request(VERSION_NEGOTIATION_MESSAGE)
		response = connection.control_channel.receive_response(id)
		if response != VERSION_NEGOTIATION_OK:
			raise ConnectionError(f"Bad handshake result {response}")

		self.connection = connection