"""Confidence is a gate, never a reason to repair characters."""
from dataclasses import dataclass, asdict
from datetime import date
import math
import re
from .date_parser import parse_date, ParseError

FIELDS = ('date', 'dm', 'ab', 'time')


def is_confidence(value):
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 <= value <= 1)


@dataclass(frozen=True)
class Expected:
    ab: str
    dm: int
    minimum_confidence: float = 0.80
    check_nsx_date: bool = False
    expected_date: date | None = None
    maximum_angle_deviation: float = 3.0

    def __post_init__(self):
        if not isinstance(self.ab, str) or not re.fullmatch('[0-9]{2}', self.ab):
            raise ValueError('AB must contain exactly two ASCII digits')
        if type(self.dm) is not int or self.dm not in (1, 2, 3):
            raise ValueError('DM must be 1, 2 or 3')
        if not is_confidence(self.minimum_confidence) or self.minimum_confidence == 0:
            raise ValueError('minimum_confidence must be in (0, 1]')
        if type(self.check_nsx_date) is not bool:
            raise ValueError('check_nsx_date must be boolean')
        if self.check_nsx_date and type(self.expected_date) is not date:
            raise ValueError('Expected date is required when date checking is enabled')
        if (type(self.maximum_angle_deviation) not in (int,float)
                or not math.isfinite(self.maximum_angle_deviation)
                or not 0<self.maximum_angle_deviation<=45):
            raise ValueError('maximum_angle_deviation must be in (0, 45]')


@dataclass(frozen=True)
class ValidationResult:
    result: str
    raw: str
    parsed: dict | None
    confidence: dict
    overall_confidence: float | None
    error_reason: str

    def to_dict(self):
        return asdict(self)


def validate(raw: str, confidence: dict, expected: Expected, *, roi_found=True):
    """Scores must come from an OCR adapter; missing scores cannot produce PASS."""
    scores = {key: confidence.get(key) for key in FIELDS}
    scores_valid = all(is_confidence(value) for value in scores.values())
    overall = min(scores.values()) if scores_valid else None
    # Sanitize invalid scores so exported results remain valid JSON.
    safe_scores = {k: v if is_confidence(v) else None for k, v in scores.items()}
    parsed = None

    def finish(code, reason=''):
        return ValidationResult(code, raw, parsed, safe_scores, overall, reason or code)

    if roi_found is not True:
        return finish('FAIL_ROI_NOT_FOUND')
    if not scores_valid:
        return finish('FAIL_OCR_LOW_CONFIDENCE', 'Missing or invalid field confidence')
    if overall < expected.minimum_confidence:
        return finish('FAIL_OCR_LOW_CONFIDENCE')
    try:
        value = parse_date(raw)
        parsed = value.to_dict()
    except ParseError as exc:
        return finish(exc.code)
    if value.dm != expected.dm:
        return finish('FAIL_DM')
    if value.ab != expected.ab:
        return finish('FAIL_AB')
    if expected.check_nsx_date and date(value.year, value.month, value.day) != expected.expected_date:
        return finish('FAIL_DATE_MISMATCH')
    return finish('PASS', 'All enabled checks passed')
