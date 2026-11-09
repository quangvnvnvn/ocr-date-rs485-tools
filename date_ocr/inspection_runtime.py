"""Shared, warmed inspection path for files and hardware-triggered frames."""
from __future__ import annotations

import gc
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from main import ROOT
from ocr.alignment import AlignmentError, fixed_alignment_region, inspect_alignment
from ocr.pipeline import DEFAULT_DEADLINE_MS, inspect_image
from ocr.preprocessing import locate_inkjet_rows
from ocr.validator import Expected
from roi.auto_roi import AutoROIDetector
from roi.fixed_roi import FixedROI


class InspectionRuntime:
    """Own the expensive OCR/ROI objects and inspect one in-memory BGR frame."""

    def __init__(self) -> None:
        self.engine = None
        self.auto_detector = None

    def prepare(self, require_auto_roi: bool = False, status=None) -> None:
        if self.engine is None:
            if status:
                status("Đang nạp và làm nóng OCR…")
            from ocr.ocr_engine import EasyOCREngine
            self.engine = EasyOCREngine()
        if require_auto_roi and self.auto_detector is None:
            if status:
                status("Đang nạp bộ bám ROI tự động…")
            manifest = json.loads((ROOT / "config/sample_rois.json").read_text(encoding="utf-8"))
            reference_roi = FixedROI.from_dict(manifest["date1.jpg"])
            self.auto_detector = AutoROIDetector(
                ROOT / "data/test_images/date1.jpg", reference_roi
            )

    def inspect(
        self,
        image: np.ndarray,
        roi: FixedROI | None,
        roi_mode: str,
        expected: Expected,
        *,
        image_name: str,
        source: str,
        received_monotonic: float | None = None,
        metadata: dict[str, Any] | None = None,
        deadline_ms: int = DEFAULT_DEADLINE_MS,
        check_alignment: bool = True,
    ) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
        """Return a fail-closed report and debug artifacts for one frame.

        The decision clock starts when the SDK hands the frame to this process.
        Camera exposure and network transfer are reported separately by the SDK
        when such timestamps are available; they are not invented here.
        """
        if self.engine is None:
            raise RuntimeError("OCR chưa được nạp nóng")
        decision_started = received_monotonic or time.perf_counter()
        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()
        try:
            auto_result = None
            auto_ms = 0.0
            alignment_image = None
            alignment_height = None
            if roi_mode == "auto":
                if self.auto_detector is None:
                    raise RuntimeError("AUTO ROI chưa được nạp nóng")
                auto_started = time.perf_counter()
                auto_result = self.auto_detector.detect(image)
                auto_ms = (time.perf_counter() - auto_started) * 1000
                if auto_result.found:
                    inspection_image = self.auto_detector.rectify(image, auto_result)
                    if check_alignment:
                        alignment_image = self.auto_detector.rectify(
                            image, auto_result, margin_bottom=1.05
                        )
                        alignment_height = self.auto_detector.reference_roi.height
                    ih, iw = inspection_image.shape[:2]
                    roi = FixedROI(0, 0, iw, ih, iw, ih)
                else:
                    inspection_image = image
                    roi = None
            else:
                inspection_image = image

            report, artifacts = inspect_image(inspection_image, roi, self.engine, expected, deadline_ms)
            if roi_mode == "fixed" and roi is not None:
                if report["validation"]["result"] == "FAIL_ROI_NOT_FOUND" and "roi" in artifacts:
                    inner = locate_inkjet_rows(artifacts["roi"])
                    # Reserve the majority of the frame budget for OCR and alignment.
                    elapsed_ms = (time.perf_counter()-decision_started)*1000
                    if inner is not None and elapsed_ms < deadline_ms*.40:
                        x, y, _, _ = report["bbox"]
                        left, top, width, height = inner
                        h, w = image.shape[:2]
                        roi = FixedROI(x+left, y+top, width, height, w, h)
                        report, artifacts = inspect_image(image, roi, self.engine, expected, deadline_ms,
                                                          prefer_gray=True)
                        report["refined_fixed_roi"] = True
                if check_alignment:
                    alignment_image, alignment_height = fixed_alignment_region(image, roi)
                    metrics = report.get("metrics")
                    if metrics:
                        # Large manual ROIs can include artwork below the band.
                        text_bottom = metrics["rows"][-1][1]
                        text_height = text_bottom + round(max(metrics["character_heights"])*.5)
                        if text_height < alignment_height*.70:
                            alignment_height = text_height
            if not check_alignment:
                report["expected"].pop("maximum_angle_deviation", None)
            report["stage_ms"]["auto_roi"] = round(auto_ms, 2)
            report["ocr_processing_ms"] = report["processing_ms"]
            alignment_started = time.perf_counter()
            if alignment_image is not None and report.get("metrics", {}).get("angle") is not None:
                try:
                    alignment, alignment_overlay = inspect_alignment(
                        alignment_image,
                        report["metrics"]["angle"],
                        alignment_height,
                        expected.maximum_angle_deviation,
                    )
                    report["alignment"] = alignment
                    artifacts["alignment"] = alignment_overlay
                    if not alignment["passed"] and report["validation"]["result"] == "PASS":
                        report["validation"].update(
                            result="FAIL_DATE_ALIGNMENT",
                            error_reason=(
                                f'Độ lệch DATE {alignment["deviation"]:.2f}° vượt '
                                f'giới hạn {alignment["maximum_deviation"]:.2f}°'
                            ),
                        )
                except AlignmentError as exc:
                    report["alignment"] = dict(
                        found=False,
                        passed=False,
                        maximum_deviation=expected.maximum_angle_deviation,
                        error_reason=str(exc),
                    )
                    artifacts["alignment"] = alignment_image
                    if report["validation"]["result"] == "PASS":
                        report["validation"].update(
                            result="FAIL_REFERENCE_LINE_NOT_FOUND", error_reason=str(exc)
                        )
            report["stage_ms"]["alignment"] = round(
                (time.perf_counter() - alignment_started) * 1000, 2
            )
            report["processing_ms"] = round(
                (time.perf_counter() - decision_started) * 1000, 2
            )
            report["deadline_met"] = report["processing_ms"] <= report["deadline_ms"]
            if not report["deadline_met"] and report["validation"]["result"] == "PASS":
                report["validation"].update(
                    result="FAIL_DEADLINE",
                    error_reason=f'Vượt thời hạn {report["deadline_ms"]} ms',
                )
            report["roi_mode"] = roi_mode
            if auto_result:
                if auto_result.found:
                    report["bbox"] = auto_result.bbox
                report["auto_roi"] = dict(
                    found=auto_result.found,
                    bbox=auto_result.bbox,
                    quad=auto_result.quad,
                    rectified=auto_result.found,
                    confidence=auto_result.confidence,
                    inliers=auto_result.inliers,
                    message=auto_result.message,
                )
                if not auto_result.found:
                    report["validation"]["error_reason"] = "AUTO ROI: " + auto_result.message
            report.update(
                image=image_name,
                source=source,
                artifacts=list({"original": image, **artifacts}),
            )
            if metadata:
                report["hardware"] = dict(metadata)
            return report, {"original": image, **artifacts}
        finally:
            if gc_was_enabled and not gc.isenabled():
                gc.enable()

