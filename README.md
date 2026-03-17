# Hegel Core

> [!IMPORTANT]
> If you've found this repository, congratulations! You're getting a sneak peek at an upcoming property-based testing library from [Antithesis](https://antithesis.com/), built on [Hypothesis](https://hypothesis.works/).
>
> We are still making rapid changes and progress.  Feel free to experiment, but don't expect stability from Hegel just yet!

Hegel is a new family of property-based testing libraries across multiple languages. Our goal is for it to be a universal approach to property-based testing which makes it nearly trivial to implement high quality property-based testing in new languages. It achieves this using a client/server approach, where all the difficult implementation can be centralised in the server, and client libraries for each language can be implemented with relatively little work.

This client/server architecture should largely be invisible when writing tests and if you are a Hegel end user, you probably don't want to install this server directly, but instead want to use one of the client libraries:

- [Go](https://github.com/hegeldev/hegel-go)
- [Rust](https://github.com/hegeldev/hegel-rust)

These will automatically manage a hegel-core install of the appropriate version using [uv](https://docs.astral.sh/uv/). 

If you do want to install Hegel yourself, you can install it like any other Python project, with your choice of `pip` or `uv`. You can point your tests to a specific install by setting the environment variable `HEGEL_SERVER_COMMAND` to point to an appropriate `hegel` executable.

## Development

```bash
just setup     # install dependencies
just test      # run tests
just format    # run formatter
just check     # run PR checks: lint + tests + docs
```
