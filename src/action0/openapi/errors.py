"""
The exception raised for OpenAPI documents this library cannot process.

Everything that goes wrong with the *input* — an unreadable schema file,
an unsupported OpenAPI version, a broken or unsupported ``$ref``, a
construct outside the supported subset — raises :py:class:`SchemaError`
with a message meant for the person running the generator. Bugs in this
library keep raising their natural exceptions.
"""


class SchemaError(Exception):
    """
    An OpenAPI document cannot be loaded or translated.

    The message names the offending file, reference or schema location
    and, for deliberate limitations, what to do instead — it is meant to
    be printed as-is by the CLI, without a traceback.
    """
