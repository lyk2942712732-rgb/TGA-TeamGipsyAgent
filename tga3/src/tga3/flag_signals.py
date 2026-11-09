"""Cheap closing-window signal, not a correctness or platform-acceptance check."""

import re

FLAG = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}\{[^{}\r\n]{1,4096}\}")
