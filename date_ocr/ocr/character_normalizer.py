"""No character substitutions until evidence supports a reviewed field policy."""


def normalize_numeric(text: str) -> str:
    # In particular O/I/l/S/? are not replaced, and interior spaces are not removed.
    return text.strip(' \t')
