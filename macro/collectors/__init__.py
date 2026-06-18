"""Data collectors for the macro sidecar. Each module exposes a pure
``parse_*`` and a network ``fetch`` with an injectable opener (tests pass a
fake opener / call the parser directly — no live network in the test suite)."""
