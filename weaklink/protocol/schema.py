"""JSON schema loader + bit-packed encoder/decoder.

Schema shape (JSON list of message templates)::

    [
      {
        "payload": "<FIELD1> <FIELD2> <FIELD3>",
        "validators": {
          "FIELD1": {"type": "string", "regex": "^[A-Z]+$", "max_length": 5},
          "FIELD2": {"type": "int", "min": 1, "max": 100},
          "FIELD3": {"type": "float", "min": -40, "max": 80, "step": 0.1}
        }
      }
    ]

On the wire the codec packs:

    [ template_id : ceil(log2(N_templates)) bits ]
    [ field 1 in payload order : field.bit_length bits ]
    [ field 2 ]
    ...
    [ zero-padded to byte boundary ]

Decoding: read the top ``template_id_bits`` to pick a template, then read each
field's bits in order. Field validators re-run on decode so garbage bytes
produce a clean error instead of a plausible-looking lie.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Protocol

try:  # Python 3.11+
    from re import _parser as _sre_parser
    from re._constants import MAXREPEAT as _MAXREPEAT
except ImportError:  # pragma: no cover - older Pythons
    import sre_parse as _sre_parser  # type: ignore[no-redef]
    from sre_constants import MAXREPEAT as _MAXREPEAT  # type: ignore[no-redef]


UPPER_ALPHABET_BITS = 5  # 26 letters -> 5 bits per char (position 0..25)


def _regex_length_bounds(pattern: str) -> tuple[int, int]:
    """Return ``(min_length, max_length)`` a string matching the regex can have.

    Raises ``ValueError`` for unbounded patterns like ``[A-Z]+`` — those need
    an explicit ``{N}`` or ``{M,N}`` quantifier so the codec knows how many
    bits to allocate on the wire.
    """
    try:
        parsed = _sre_parser.parse(pattern)
    except Exception as exc:
        raise ValueError(f"could not parse regex {pattern!r}: {exc}") from exc
    bounds = _bounds_of(parsed)
    if bounds is None:
        raise ValueError(
            f"regex {pattern!r} has an unbounded quantifier; use ``{{N}}`` or ``{{M,N}}`` "
            f"so the codec knows the max length"
        )
    return bounds


def _extract_literal(pattern: str) -> str | None:
    """If the regex matches exactly one string, return it. Otherwise ``None``.

    A regex is a pure literal when its AST contains only LITERAL nodes, AT
    (zero-width) anchors, and repeats with min == max of literal subtrees. In
    that case the encoded value takes zero bits — the string is baked into the
    schema — and encoded packets shrink accordingly.
    """
    try:
        parsed = _sre_parser.parse(pattern)
    except Exception:
        return None
    literal = _literal_from_ast(parsed)
    if literal is None:
        return None
    if not re.fullmatch(pattern, literal):
        return None
    return literal


def _literal_from_ast(subpattern) -> str | None:
    parts: list[str] = []
    for op, args in subpattern:
        op_name = op.name if hasattr(op, "name") else str(op)
        if op_name == "AT":
            continue
        if op_name == "LITERAL":
            parts.append(chr(args))
        elif op_name == "SUBPATTERN":
            child = _literal_from_ast(args[3])
            if child is None:
                return None
            parts.append(child)
        elif op_name in ("MAX_REPEAT", "MIN_REPEAT"):
            min_rep, max_rep, subitems = args
            if min_rep != max_rep:
                return None
            child = _literal_from_ast(subitems)
            if child is None:
                return None
            parts.append(child * min_rep)
        else:
            return None
    return "".join(parts)


def _bounds_of(subpattern) -> tuple[int, int] | None:
    """Walk the parsed regex AST, returning (min_len, max_len) or None if unbounded."""
    min_total = 0
    max_total = 0
    for op, args in subpattern:
        op_name = op.name if hasattr(op, "name") else str(op)
        if op_name in ("LITERAL", "NOT_LITERAL", "IN", "ANY", "CATEGORY"):
            min_total += 1
            max_total += 1
        elif op_name == "AT":
            pass  # zero-width anchor
        elif op_name in ("MAX_REPEAT", "MIN_REPEAT"):
            min_rep, max_rep, subitems = args
            if max_rep == _MAXREPEAT:
                return None
            child = _bounds_of(subitems)
            if child is None:
                return None
            child_min, child_max = child
            min_total += child_min * min_rep
            max_total += child_max * max_rep
        elif op_name == "BRANCH":
            _first, branches = args
            branch_bounds = [_bounds_of(b) for b in branches]
            if any(b is None for b in branch_bounds):
                return None
            branch_mins = [b[0] for b in branch_bounds]  # type: ignore[index]
            branch_maxes = [b[1] for b in branch_bounds]  # type: ignore[index]
            min_total += min(branch_mins)
            max_total += max(branch_maxes)
        elif op_name == "SUBPATTERN":
            # args = (group_id, add_flags, del_flags, subpattern)
            child = _bounds_of(args[3])
            if child is None:
                return None
            child_min, child_max = child
            min_total += child_min
            max_total += child_max
        elif op_name == "ASSERT" or op_name == "ASSERT_NOT":
            pass  # zero-width lookaround
        else:
            # Unknown op — be safe and treat as unbounded.
            return None
    return min_total, max_total


class Field(Protocol):
    name: str

    @property
    def bit_length(self) -> int: ...

    def encode(self, text_value: str) -> int: ...

    def decode(self, int_value: int) -> str: ...


@dataclass(frozen=True)
class IntField:
    name: str
    min_value: int
    max_value: int

    def __post_init__(self) -> None:
        if self.max_value < self.min_value:
            raise ValueError(f"{self.name}: max {self.max_value} < min {self.min_value}")

    @property
    def bit_length(self) -> int:
        span = self.max_value - self.min_value + 1
        return max(1, (span - 1).bit_length())

    def encode(self, text_value: str) -> int:
        value = int(text_value)
        if not (self.min_value <= value <= self.max_value):
            raise ValueError(
                f"{self.name}={value} out of range [{self.min_value}, {self.max_value}]"
            )
        return value - self.min_value

    def decode(self, int_value: int) -> str:
        return str(int_value + self.min_value)


@dataclass(frozen=True)
class FloatField:
    name: str
    min_value: float
    max_value: float
    step: float = 1.0

    def __post_init__(self) -> None:
        if self.max_value <= self.min_value:
            raise ValueError(f"{self.name}: max {self.max_value} <= min {self.min_value}")
        if self.step <= 0:
            raise ValueError(f"{self.name}: step {self.step} must be > 0")

    @property
    def num_steps(self) -> int:
        return int(round((self.max_value - self.min_value) / self.step)) + 1

    @property
    def bit_length(self) -> int:
        return max(1, (self.num_steps - 1).bit_length())

    @property
    def _decimals(self) -> int:
        # Print with enough decimals to reflect the step size.
        if self.step >= 1:
            return 0
        return max(0, int(math.ceil(-math.log10(self.step))))

    def encode(self, text_value: str) -> int:
        value = float(text_value)
        if not (self.min_value <= value <= self.max_value):
            raise ValueError(
                f"{self.name}={value} out of range [{self.min_value}, {self.max_value}]"
            )
        return int(round((value - self.min_value) / self.step))

    def decode(self, int_value: int) -> str:
        value = self.min_value + int_value * self.step
        return f"{value:.{self._decimals}f}"


@dataclass(frozen=True)
class StringField:
    """A-Z-only string. Length bounds are inferred from the regex.

    ``regex`` must have a bounded max length — ``^[A-Z]{5}$``, ``^[A-Z]{2,10}$``,
    or ``^HELLO$`` all work. Unbounded quantifiers like ``[A-Z]+`` are rejected
    at load time. If min and max lengths match (fixed-length field), the codec
    skips the length prefix on the wire.
    """

    name: str
    regex: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", re.compile(self.regex))
        literal = _extract_literal(self.regex)
        if literal is not None:
            object.__setattr__(self, "_literal", literal)
            object.__setattr__(self, "_min_length", len(literal))
            object.__setattr__(self, "_max_length", len(literal))
        else:
            object.__setattr__(self, "_literal", None)
            min_len, max_len = _regex_length_bounds(self.regex)
            if max_len < 1:
                raise ValueError(
                    f"{self.name}: regex allows zero-length strings, which the codec cannot carry"
                )
            object.__setattr__(self, "_min_length", min_len)
            object.__setattr__(self, "_max_length", max_len)

    @property
    def min_length(self) -> int:
        return self._min_length  # type: ignore[attr-defined]

    @property
    def max_length(self) -> int:
        return self._max_length  # type: ignore[attr-defined]

    @property
    def is_literal(self) -> bool:
        return self._literal is not None  # type: ignore[attr-defined]

    @property
    def _is_fixed_length(self) -> bool:
        return self.min_length == self.max_length

    @property
    def _length_bits(self) -> int:
        if self._is_fixed_length:
            return 0
        span = self.max_length - self.min_length + 1
        return max(1, (span - 1).bit_length())

    @property
    def bit_length(self) -> int:
        if self.is_literal:
            return 0
        return self._length_bits + UPPER_ALPHABET_BITS * self.max_length

    def encode(self, text_value: str) -> int:
        if not self._compiled.fullmatch(text_value):
            raise ValueError(f"{self.name}={text_value!r} does not match regex {self.regex!r}")
        if self.is_literal:
            if text_value != self._literal:  # type: ignore[attr-defined]
                raise ValueError(
                    f"{self.name}={text_value!r} does not match literal {self._literal!r}"
                )
            return 0
        if len(text_value) < self.min_length or len(text_value) > self.max_length:
            raise ValueError(
                f"{self.name}={text_value!r} length {len(text_value)} outside "
                f"[{self.min_length}, {self.max_length}]"
            )
        for character in text_value:
            if not ("A" <= character <= "Z"):
                raise ValueError(
                    f"{self.name}={text_value!r} contains non-uppercase character {character!r}"
                )
        result = 0
        if not self._is_fixed_length:
            result = len(text_value) - self.min_length
        for position in range(self.max_length):
            character_value = ord(text_value[position]) - ord("A") if position < len(text_value) else 0
            result = (result << UPPER_ALPHABET_BITS) | character_value
        return result

    def decode(self, int_value: int) -> str:
        if self.is_literal:
            return self._literal  # type: ignore[attr-defined]
        remaining = int_value
        chars: list[str] = []
        for _ in range(self.max_length):
            character_value = remaining & ((1 << UPPER_ALPHABET_BITS) - 1)
            chars.append(chr(character_value + ord("A")))
            remaining >>= UPPER_ALPHABET_BITS
        chars.reverse()
        if self._is_fixed_length:
            length = self.max_length
        else:
            length = remaining + self.min_length
        decoded = "".join(chars[:length])
        if not self._compiled.fullmatch(decoded):
            raise ValueError(
                f"decoded {self.name}={decoded!r} does not match regex {self.regex!r}"
            )
        return decoded


def _make_field(name: str, spec: dict) -> Field:
    field_type = spec.get("type")
    if field_type == "int":
        return IntField(name=name, min_value=int(spec["min"]), max_value=int(spec["max"]))
    if field_type == "float":
        return FloatField(
            name=name,
            min_value=float(spec["min"]),
            max_value=float(spec["max"]),
            step=float(spec.get("step", 1.0)),
        )
    if field_type == "string":
        return StringField(name=name, regex=spec["regex"])
    raise ValueError(f"unknown field type {field_type!r} for {name}")


@dataclass
class MessageTemplate:
    payload: str
    fields: list[Field]
    _extractor: re.Pattern = dataclass_field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Build a regex that extracts each <FIELDn> placeholder as a named group.
        pattern_parts: list[str] = []
        cursor = 0
        for match in re.finditer(r"<(\w+)>", self.payload):
            pattern_parts.append(re.escape(self.payload[cursor : match.start()]))
            pattern_parts.append(f"(?P<{match.group(1)}>\\S+)")
            cursor = match.end()
        pattern_parts.append(re.escape(self.payload[cursor:]))
        self._extractor = re.compile("^" + "".join(pattern_parts) + "$")

    @classmethod
    def from_json(cls, obj: dict) -> "MessageTemplate":
        payload = obj["payload"]
        validators = obj["validators"]
        field_names = re.findall(r"<(\w+)>", payload)
        seen: set[str] = set()
        for name in field_names:
            if name in seen:
                raise ValueError(f"duplicate field {name!r} in payload {payload!r}")
            seen.add(name)
            if name not in validators:
                raise ValueError(f"field {name!r} in payload has no validator")
        fields = [_make_field(name, validators[name]) for name in field_names]
        return cls(payload=payload, fields=fields)

    @property
    def bit_length(self) -> int:
        return sum(field_obj.bit_length for field_obj in self.fields)

    def extract(self, text: str) -> dict[str, str] | None:
        match = self._extractor.match(text)
        if match is None:
            return None
        return match.groupdict()


@dataclass
class Schema:
    templates: list[MessageTemplate]

    @classmethod
    def from_json(cls, data) -> "Schema":
        if not isinstance(data, list):
            raise ValueError("schema JSON must be a list of message templates")
        return cls(templates=[MessageTemplate.from_json(item) for item in data])

    @property
    def template_id_bits(self) -> int:
        count = len(self.templates)
        if count <= 1:
            return 0
        return (count - 1).bit_length()

    def encode(self, text: str) -> bytes:
        errors: list[str] = []
        for template_index, template in enumerate(self.templates):
            values = template.extract(text)
            if values is None:
                errors.append(f"template {template_index}: payload shape did not match")
                continue
            try:
                return self._encode_one(template_index, template, values)
            except ValueError as exc:
                errors.append(f"template {template_index}: {exc}")
        joined = "; ".join(errors)
        raise ValueError(f"no template accepted {text!r}: {joined}")

    def _encode_one(
        self, template_index: int, template: MessageTemplate, values: dict[str, str]
    ) -> bytes:
        buffer = 0
        if self.template_id_bits > 0:
            buffer = template_index
        for field_obj in template.fields:
            bits_for_field = field_obj.encode(values[field_obj.name])
            buffer = (buffer << field_obj.bit_length) | bits_for_field
        total_bits = self.template_id_bits + template.bit_length
        total_bytes = (total_bits + 7) // 8
        padding = total_bytes * 8 - total_bits
        return (buffer << padding).to_bytes(total_bytes, byteorder="big")

    def decode(self, data: bytes) -> str:
        if not data:
            raise ValueError("empty data")
        raw = int.from_bytes(data, byteorder="big")
        bits_available = len(data) * 8

        if self.template_id_bits > 0:
            template_index = raw >> (bits_available - self.template_id_bits)
            if template_index >= len(self.templates):
                raise ValueError(f"invalid template index {template_index}")
        else:
            template_index = 0

        template = self.templates[template_index]
        total_bits = self.template_id_bits + template.bit_length
        padding = bits_available - total_bits
        if padding < 0:
            raise ValueError(
                f"data too short: got {bits_available} bits, template expects {total_bits}"
            )
        aligned = raw >> padding

        decoded_values: dict[str, str] = {}
        for field_obj in reversed(template.fields):
            mask = (1 << field_obj.bit_length) - 1
            decoded_values[field_obj.name] = field_obj.decode(aligned & mask)
            aligned >>= field_obj.bit_length

        rendered = template.payload
        for name, value in decoded_values.items():
            rendered = rendered.replace(f"<{name}>", value)
        return rendered


def load_schema(path: Path | str) -> Schema:
    with open(path, "r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    return Schema.from_json(data)
