RELEASE_TYPE: patch

Fix crash when a reply arrives on a channel that the local side has already closed. This could happen when an SDK sends a fire-and-forget request (e.g. `mark_complete`) and immediately closes the channel before the server's reply arrives.
