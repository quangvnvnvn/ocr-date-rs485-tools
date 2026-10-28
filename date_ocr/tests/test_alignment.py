import math
from pathlib import Path
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from ocr.alignment import detect_reference_line,inspect_alignment,AlignmentError


class AlignmentTests(unittest.TestCase):
    @staticmethod
    def reference_image(angle=-2.0):
        image=np.full((550,772,3),245,np.uint8)
        delta=math.tan(math.radians(angle))*771
        cv2.line(image,(0,round(365-delta/2)),(771,round(365+delta/2)),
                 (45,45,45),80,cv2.LINE_AA)
        return image

    def test_detects_long_reference_bar(self):
        angle,_,confidence,_=detect_reference_line(self.reference_image(),260)
        self.assertAlmostEqual(angle,-2.0,delta=.35)
        self.assertGreater(confidence,.9)

    def test_deviation_gate_is_inclusive_and_fail_closed(self):
        measurement,_=inspect_alignment(self.reference_image(),-4.5,260,3.0)
        self.assertTrue(measurement['passed'])
        measurement,_=inspect_alignment(self.reference_image(),2.0,260,3.0)
        self.assertFalse(measurement['passed'])
        self.assertGreater(measurement['deviation'],3.0)

    def test_missing_reference_bar_is_rejected(self):
        with self.assertRaises(AlignmentError):
            detect_reference_line(np.full((550,772,3),255,np.uint8),260)

    def test_thin_graphic_line_is_not_the_dark_band(self):
        image=np.full((550,772,3),245,np.uint8)
        cv2.line(image,(0,365),(771,365),(35,35,35),2,cv2.LINE_AA)
        with self.assertRaises(AlignmentError):
            detect_reference_line(image,260)

    def test_ignores_thin_line_and_uses_dark_band_edge(self):
        image=self.reference_image(-2.0)
        cv2.line(image,(0,325),(771,355),(20,20,20),2,cv2.LINE_AA)
        angle,_,_,_=detect_reference_line(image,260)
        self.assertAlmostEqual(angle,-2.0,delta=.35)

    def test_detects_low_contrast_hikrobot_dark_band(self):
        fixture_dir=Path(__file__).resolve().parent/'fixtures/hikrobot_alignment'
        expected={'dark_band_uneven.png':-1.5,'dark_band_weak_edge.png':.75}
        for name,expected_angle in expected.items():
            with self.subTest(name=name):
                image=cv2.imdecode(np.fromfile(str(fixture_dir/name),dtype=np.uint8),
                                   cv2.IMREAD_COLOR)
                angle,line,confidence,_=detect_reference_line(image,260)
                self.assertAlmostEqual(angle,expected_angle,delta=.7)
                self.assertGreaterEqual(confidence,.85)
                self.assertGreater(line[1],300)

    def test_hikrobot_band_has_deterministic_fallback(self):
        fixture=Path(__file__).resolve().parent/'fixtures/hikrobot_alignment/dark_band_weak_edge.png'
        image=cv2.imdecode(np.fromfile(str(fixture),dtype=np.uint8),cv2.IMREAD_COLOR)
        with patch('ocr.alignment.cv2.HoughLinesP',return_value=None):
            angle,line,confidence,_=detect_reference_line(image,260)
        self.assertAlmostEqual(angle,.9,delta=.5)
        self.assertGreaterEqual(confidence,.85)
        self.assertGreater(line[2]-line[0],700)


if __name__=='__main__':
    unittest.main()
