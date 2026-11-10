"""Resolve the ordinary worker paths inside a single hosted sandbox."""

import json
import os
import re


def local_paths(value):
    mapping = json.loads(os.environ.get("TGA3_LOCAL_PATHS", "{}"))
    if not mapping:
        return value
    if isinstance(value, str):
        # Do not rewrite target URLs or a path already translated to /app/data/...
        for logical, physical in sorted(mapping.items(), key=lambda pair: -len(pair[0])):
            pattern = r"(?<![\w/.-])" + re.escape(logical) + r'(?=$|[/\s"\x27\\,;:.<>*}\]])'
            value = re.sub(pattern, lambda _, physical=physical: physical, value)
        return value
    if isinstance(value, list):
        return [local_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: local_paths(item) for key, item in value.items()}
    return value
