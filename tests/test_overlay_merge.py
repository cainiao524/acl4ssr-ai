"""Boundary and input-type regression tests for overlay line classification."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assets.overlay_merge import is_blank_or_comment


class IsBlankOrCommentTests(unittest.TestCase):
    def test_blank_and_comment_boundaries(self):
        cases = {
            "": True,
            " \t\r\n": True,
            "  ; comment": True,
            "\t# comment": True,
            ";": True,
            "#": True,
            "value": False,
            "  value  ": False,
            "value ; trailing comment": False,
            ";comment after value": True,
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertIs(is_blank_or_comment(line), expected)

    def test_non_string_inputs_raise_from_their_string_operations(self):
        cases = ((None, AttributeError), (0, AttributeError),
                 (b"# comment", TypeError))
        for line, error_type in cases:
            with self.subTest(line=line):
                with self.assertRaises(error_type):
                    is_blank_or_comment(line)


if __name__ == "__main__":
    unittest.main()
