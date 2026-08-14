"""
Generate fully typed :py:mod:`action0.client` API clients from OpenAPI schemas.

Where `action0-client <https://laughinjar.github.io/action0-client/>`_
lets you *hand-write* an API as typed operation dataclasses, this package
*generates* that code from an OpenAPI schema file: one operation class per
endpoint, plus the model classes their results are parsed into. The
generated code is plain, readable ``action0-client`` code — it depends on
``action0-client`` only, not on this package, and runs on whichever
backend (sync, asyncio, Twisted, ...) is plugged in.
"""

__version__: str = "0.1.0"

__all__: list[str] = []
