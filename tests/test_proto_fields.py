"""Proto: utf8_field edge cases (a suffix longer than the field must not turn the cut into a negative slice).
    python3 tests/test_proto_fields.py receiver"""
import sys
sys.path.insert(0, sys.argv[1])
from proto import utf8_field

assert utf8_field("abc", 8) == b"abc"
assert utf8_field("abcdefghij", 6, "…") == "abc…".encode()
assert utf8_field("abcdefghij", 2, "…") == "…".encode(), "suffix wider than the field: cut to nothing, no negative slice"
assert utf8_field("abcdefghij", 0) == b""
assert utf8_field(None, 4) == b""
print("PROTO FIELDS OK")
