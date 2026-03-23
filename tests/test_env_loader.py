from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from sultan_engine_v2.runtime.env_loader import load_env_file, parse_env_line


class EnvLoaderTests(unittest.TestCase):
    def test_parse_env_line_supports_export_and_quotes(self) -> None:
        self.assertEqual(("TEST_ONE", "alpha"), parse_env_line("export TEST_ONE=alpha"))
        self.assertEqual(("TEST_TWO", "two words"), parse_env_line('TEST_TWO="two words"'))
        self.assertEqual(("TEST_THREE", "three words"), parse_env_line("TEST_THREE='three words'"))
        self.assertIsNone(parse_env_line("# comment"))
        self.assertIsNone(parse_env_line("invalid line"))

    def test_load_env_file_populates_environment_without_overriding_existing_values(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "sample_runtime.env"
        with patch.dict(os.environ, {"EXISTING_KEY": "keep"}, clear=True):
            loaded = load_env_file(fixture)

            self.assertEqual("one", os.environ["TEST_ALPHA"])
            self.assertEqual("two words", os.environ["TEST_BETA"])
            self.assertEqual("three", os.environ["TEST_GAMMA"])
            self.assertEqual("keep", os.environ["EXISTING_KEY"])
            self.assertEqual("from_file", loaded["EXISTING_KEY"])


if __name__ == "__main__":
    unittest.main()
