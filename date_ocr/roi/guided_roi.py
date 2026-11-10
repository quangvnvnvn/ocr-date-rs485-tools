"""Find the two-line DATE stamp inside an operator-selected search area."""
from pathlib import Path

import cv2
import numpy as np


class GuidedROIDetector:
    def __init__(self, calibration: Path):
        anchors = []
        for name in ("nsx", "dm"):
            path = calibration / f"hikrobot_001_{name}_anchor.png"
            gray = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise ValueError(f"Không đọc được mẫu chữ {name.upper()}")
            _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
            x, y, width, height = cv2.boundingRect(ink)
            if not width or not height:
                raise ValueError(f"Mẫu chữ {name.upper()} bị rỗng")
            anchors.append(ink[y:y+height, x:x+width])
        self.templates = []
        for height in (10, 12, 14, 16, 19, 22, 26, 31, 37, 44, 52, 62, 74, 88):
            dm_height = round(height*.94)
            nsx, dm = [
                cv2.resize(ink, (round(ink.shape[1]*size/ink.shape[0]), size),
                           interpolation=cv2.INTER_AREA)
                for ink, size in zip(anchors, (height, dm_height))
            ]
            self.templates.append((height, nsx, dm))
        self.kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 13))

    def detect(self, image):
        """Return a crop relative to the search area, or None without both anchors."""
        height, width = image.shape[:2]
        if min(height, width) < 32:
            return None
        # Keep small camera glyphs legible even when the operator selects
        # almost the entire width of a 2448 px frame.
        long_side = max(height, width)
        working_side = min(560, max(400, round(long_side*.22)))
        scale = min(1., working_side/long_side)
        if min(height, width)*scale < 12:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ink = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, self.kernel)
        np.minimum(ink, 75, out=ink)
        _, _, stats, _ = cv2.connectedComponentsWithStats(np.uint8(ink > 30))
        glyph_w, glyph_h = stats[1:, 2:4].T
        likely = glyph_h[(glyph_h >= max(8, round(25*scale)))
                         & (glyph_h <= gray.shape[0]*.40)
                         & (glyph_w >= 2) & (glyph_w <= 2.5*glyph_h)]
        center = float(np.median(likely)) if len(likely) >= 4 else 48*scale
        levels = sorted(
            (item for item in self.templates
             if item[0]/scale >= 24 and item[0] < gray.shape[0] and item[1].shape[1] < gray.shape[1]),
            key=lambda item: abs(np.log(item[0]/center)),
        )[:6]

        matches = []
        for size, nsx, dm in levels:
            scores = cv2.matchTemplate(ink, nsx, cv2.TM_CCOEFF_NORMED)
            for _ in range(2):
                _, nsx_score, _, (x, y) = cv2.minMaxLoc(scores)
                if nsx_score < .40:
                    break
                # The DM label follows six date digits on the NSX line.
                left, top = x+round(5.6*size), max(0, y-round(.8*size))
                right = min(gray.shape[1], x+round(9.8*size))
                bottom = min(gray.shape[0], y+round(1.8*size))
                search = ink[top:bottom, left:right]
                if search.shape[0] >= dm.shape[0] and search.shape[1] >= dm.shape[1]:
                    _, dm_score, _, (offset_x, offset_y) = cv2.minMaxLoc(
                        cv2.matchTemplate(search, dm, cv2.TM_CCOEFF_NORMED))
                    if dm_score >= .40:
                        dm_x, dm_y = left+offset_x, top+offset_y
                        x0 = max(0, round((x-.48*size)/scale))
                        y0 = max(0, round((min(y, dm_y)-.45*size)/scale))
                        x1 = min(width, round((dm_x+dm.shape[1]+1.70*size)/scale))
                        y1 = min(height, round((max(y, dm_y)+2.95*size)/scale))
                        matches.append((min(nsx_score, dm_score), (x0, y0, x1-x0, y1-y0)))
                scores[max(0, y-size):y+size, max(0, x-2*size):x+2*size] = -1
        return max(matches)[1] if matches else None
