RELEASE_TYPE: patch

The reader loop now exits gracefully when the remote end closes the connection, instead of raising an unhandled exception in the reader thread.
