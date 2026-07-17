"""Schema-driven bit-packed message codec.

Schema JSON: list of message templates. Each template has a ``payload`` string
with ``<FIELDn>`` placeholders and a ``validators`` object describing each
field's type and constraints. Field types are ``int`` and ``float`` with
``min``/``max`` (and optional ``step`` for float), and ``string`` with
``regex`` + ``max_length``. Strings are always A-Z.

The encoded bytes are meant to drop into ``weaklink-modem`` as the payload,
but the codec is transport-agnostic.
"""

from weaklink.protocol.schema import (
    FloatField,
    IntField,
    MessageTemplate,
    Schema,
    StringField,
    load_schema,
)

__all__ = [
    "Schema",
    "MessageTemplate",
    "IntField",
    "FloatField",
    "StringField",
    "load_schema",
]
