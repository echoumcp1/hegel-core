from hegel.protocol import Connection, VERSION_NEGOTIATION_MESSAGE

def run_server_on_connection(connection: Connection):
	try:
		control = connection.control_channel
		id, payload = control.receive_request()
		if payload == VERSION_NEGOTIATION_MESSAGE:
			control.send_response(id, b"Ok")
		else:
			control.send_response(id, b"Error: Unrecognised negotiation string {repr(payload)}".encode('utf-8'))

		while True:
			id, payload = control.receive_request()
			control.send_response(id, b"I don't know what to do with that")
	except ConnectionError:
		pass
	finally:
		connection.close()