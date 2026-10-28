from datetime import date
import unittest
from ocr.date_parser import parse_date, ParseError
from ocr.validator import Expected, validate
from ocr.character_normalizer import normalize_numeric

RAW = 'NSX 220826 DM 1\n12 12:52'
SCORES = dict(date=.98, dm=.99, ab=.97, time=.96)


class ParserTests(unittest.TestCase):
    def test_valid_formats(self):
        for raw in (RAW, 'NSX220826 DM1\n12 12:52',
                    'NSX   220826   DM   1\r\n12     12:52',
                    'NSX 010926 DM 3\n05 08:01'):
            with self.subTest(raw=raw):
                self.assertTrue(parse_date(raw).valid)

    def test_fields(self):
        parsed = parse_date(RAW)
        self.assertEqual((parsed.nsx_raw, parsed.day, parsed.month, parsed.year,
                          parsed.dm, parsed.ab, parsed.time),
                         ('220826', 22, 8, 2026, 1, '12', '12:52'))
        self.assertEqual(parsed.raw, RAW)

    def test_rejections(self):
        cases = [('220826', '350826', 'FAIL_DATE'),
                 ('220826', '310226', 'FAIL_DATE'),
                 ('220826', '000826', 'FAIL_DATE'),
                 ('DM 1', 'DM 5', 'FAIL_FORMAT'),
                 ('12 12:52', '123 12:52', 'FAIL_FORMAT'),
                 ('12 12:52', '1 12:52', 'FAIL_FORMAT'),
                 ('12:52', '25:99', 'FAIL_TIME'),
                 ('12:52', '12:72', 'FAIL_TIME'),
                 ('220826', '22082', 'FAIL_FORMAT'),
                 ('220826', '2208?6', 'FAIL_OCR'),
                 ('12:52', '12:?2', 'FAIL_OCR'),
                 ('220826', '22O826', 'FAIL_FORMAT'),
                 ('12:52', '1252', 'FAIL_FORMAT'),
                 ('220826', '２２０８２６', 'FAIL_FORMAT')]
        for before, after, code in cases:
            with self.subTest(after=after), self.assertRaises(ParseError) as error:
                parse_date(RAW.replace(before, after))
            self.assertEqual(error.exception.code, code)

    def test_no_substring_acceptance(self):
        for raw in ('other label\n' + RAW, RAW + '\nextra', 'extra ' + RAW,
                    RAW + ' extra', RAW.replace('\n', ' ')):
            with self.subTest(raw=raw), self.assertRaises(ParseError):
                parse_date(raw)

    def test_leap_year_and_boundaries(self):
        self.assertTrue(parse_date(RAW.replace('220826', '290224')).valid)
        with self.assertRaises(ParseError):
            parse_date(RAW.replace('220826', '290225'))
        for clock in ('00:00', '23:59'):
            self.assertEqual(parse_date(RAW.replace('12:52', clock)).time, clock)


class ValidatorTests(unittest.TestCase):
    def test_pass_minimum_and_no_mutation(self):
        result = validate(RAW, SCORES, Expected('12', 1))
        self.assertEqual(result.result, 'PASS')
        self.assertEqual(result.overall_confidence, .96)
        self.assertEqual(result.raw, RAW)

    def test_mismatches(self):
        for expected, code in ((Expected('12', 2), 'FAIL_DM'),
                               (Expected('13', 1), 'FAIL_AB'),
                               (Expected('12', 1, check_nsx_date=True,
                                         expected_date=date(2026, 8, 21)), 'FAIL_DATE_MISMATCH')):
            with self.subTest(code=code):
                self.assertEqual(validate(RAW, SCORES, expected).result, code)

    def test_low_or_invalid_confidence_never_passes(self):
        for field in SCORES:
            for score in (.79, None, float('nan'), float('inf'), -1, 1.1, True, '0.99'):
                with self.subTest(field=field, score=score):
                    scores = {**SCORES, field: score}
                    self.assertEqual(validate(RAW, scores, Expected('12', 1)).result,
                                     'FAIL_OCR_LOW_CONFIDENCE')
        self.assertEqual(validate(RAW, {}, Expected('12', 1)).result,
                         'FAIL_OCR_LOW_CONFIDENCE')

    def test_threshold_inclusive(self):
        self.assertEqual(validate(RAW, dict.fromkeys(SCORES, .8), Expected('12', 1)).result, 'PASS')

    def test_expected_date_enabled_and_disabled(self):
        self.assertEqual(validate(RAW, SCORES, Expected('12', 1, check_nsx_date=True,
                         expected_date=date(2026, 8, 22))).result, 'PASS')
        self.assertEqual(validate(RAW, SCORES, Expected('12', 1,
                         expected_date=date(2000, 1, 1))).result, 'PASS')

    def test_missing_roi(self):
        self.assertEqual(validate(RAW, SCORES, Expected('12', 1), roi_found=False).result,
                         'FAIL_ROI_NOT_FOUND')

    def test_invalid_operator_settings(self):
        for settings in (dict(ab='1', dm=1), dict(ab='123', dm=1), dict(ab='１２', dm=1),
                         dict(ab='12', dm=True), dict(ab='12', dm=5),
                         dict(ab='12', dm=1, minimum_confidence=0),
                         dict(ab='12', dm=1, minimum_confidence=float('nan')),
                         dict(ab='12', dm=1, maximum_angle_deviation=0),
                         dict(ab='12', dm=1, maximum_angle_deviation=float('nan')),
                         dict(ab='12', dm=1, check_nsx_date=True)):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                Expected(**settings)

    def test_unknown_characters_not_repaired(self):
        self.assertEqual(normalize_numeric(' OIlS? '), 'OIlS?')
        self.assertEqual(validate(RAW.replace('220826', '2208?6'), SCORES,
                                  Expected('12', 1)).result, 'FAIL_OCR')


if __name__ == '__main__':
    unittest.main()
