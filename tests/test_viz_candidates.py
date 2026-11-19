import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.viz_candidates import load_candidates, write_review_log


class VizCandidatesTest(unittest.TestCase):
    def test_loads_pipeline_candidate_format(self) -> None:
        payload = {
            "params": {"match_iou": 0.25},
            "images": 250,
            "candidates": [
                {
                    "image": "000000000139.jpg",
                    "type": "class_swap|category_swap",
                    "why": "detection and panoptic disagree",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = load_candidates(path)

        self.assertEqual(
            result,
            [{
                "target": "000000000139",
                "type": "class_swap|category_swap",
                "detail": "detection and panoptic disagree",
            }],
        )

    def test_loads_legacy_target_detail_format(self) -> None:
        payload = {"candidates": [{"target": "img001", "type": "bbox_drift", "detail": "drift"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = load_candidates(path)

        self.assertEqual(result[0]["target"], "img001")
        self.assertEqual(result[0]["detail"], "drift")

    def test_review_log_has_header_and_deduplicates_rows(self) -> None:
        candidates = [
            {"target": "img001", "type": "missing_box", "detail": ""},
            {"target": "img001", "type": "missing_box", "detail": "duplicate"},
            {"target": "img002", "type": "bbox_drift", "detail": ""},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review_log.csv"
            write_review_log(path, candidates)
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))

        self.assertEqual(rows[0], ["image", "type", "verdict", "note"])
        self.assertEqual(rows[1:], [["img001", "missing_box", "", ""], ["img002", "bbox_drift", "", ""]])


if __name__ == "__main__":
    unittest.main()
