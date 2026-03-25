RELEASE_TYPE: patch

This release adds a --stdio flag to hegel-core that allows the calling process to communicate with it directly via stdin and stdout rather than going via a unix socket.

As well as simplifying the interactions with hegel-core, this should enable easier support for Windows later.
