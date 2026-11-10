"""Parse structure without guessing, substitutions, or substring matching."""
from dataclasses import dataclass, asdict
from datetime import date
import re

FIRST_LINE = re.compile(r'[ \t]*(?P<nsx_anchor>NSX)[ \t]*(?P<date>[0-9]{6})[ \t]*(?P<dm_anchor>DM)[ \t]*(?P<dm>[123])[ \t]*')
SECOND_LINE = re.compile(r'[ \t]*(?P<ab>[0-9]{2})[ \t]+(?P<time>[0-9]{2}:[0-9]{2})[ \t]*')


class ParseError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ParsedDate:
    raw: str
    nsx_raw: str
    day: int
    month: int
    year: int
    dm: int
    ab: str
    hour: int
    minute: int
    time: str
    valid: bool = True

    def to_dict(self):
        return asdict(self)


def parse_date(raw: str) -> ParsedDate:
    if not isinstance(raw, str) or not raw.strip() or '?' in raw or '\ufffd' in raw:
        raise ParseError('FAIL_OCR')
    lines = raw.strip().splitlines()
    if len(lines) != 2:
        raise ParseError('FAIL_FORMAT')
    first = FIRST_LINE.fullmatch(lines[0])
    second = SECOND_LINE.fullmatch(lines[1])
    if not first or not second:
        raise ParseError('FAIL_FORMAT')
    digits, dm = first.group('date', 'dm')
    ab, clock = second.groups()
    hour, minute = clock.split(':')
    day, month, year = int(digits[:2]), int(digits[2:4]), 2000 + int(digits[4:])
    try:
        date(year, month, day)
    except ValueError as exc:
        raise ParseError('FAIL_DATE') from exc
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        raise ParseError('FAIL_TIME')
    return ParsedDate(raw, digits, day, month, year, int(dm), ab,
                      int(hour), int(minute), f'{hour}:{minute}')
