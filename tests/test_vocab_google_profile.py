import json
import os
import sys
import types
import unittest
from unittest.mock import patch

if "pypinyin" not in sys.modules:
    fake_pypinyin = types.ModuleType("pypinyin")

    class _FakeStyle:
        NORMAL = "NORMAL"

    fake_pypinyin.Style = _FakeStyle
    fake_pypinyin.lazy_pinyin = lambda text, *args, **kwargs: [str(text)]
    sys.modules["pypinyin"] = fake_pypinyin

from pipelines import vocab_pipeline


class VocabGoogleProfileTests(unittest.TestCase):
    def test_requested_gender_uses_its_own_saved_google_slot(self):
        profiles = {
            "vi": {
                "gender": "Nữ",
                "voice_name": "vi-FEMALE-default",
                "slots": {
                    "Mặc định": {"gender": "Mặc định", "voice_name": ""},
                    "Nam": {"gender": "Nam", "voice_name": "vi-MALE-custom"},
                    "Nữ": {"gender": "Nữ", "voice_name": "vi-FEMALE-custom"},
                    "Trung tính": {"gender": "Trung tính", "voice_name": ""},
                },
            }
        }
        with patch.dict(
            os.environ,
            {"GOOGLE_TTS_PROFILES_JSON": json.dumps(profiles, ensure_ascii=False)},
            clear=False,
        ):
            self.assertEqual(
                {"gender": "Nam", "voice_name": "vi-MALE-custom"},
                vocab_pipeline._get_google_profile("vi", "Nam"),
            )
            self.assertEqual(
                {"gender": "Nữ", "voice_name": "vi-FEMALE-custom"},
                vocab_pipeline._get_google_profile("vi", "Nữ"),
            )


if __name__ == "__main__":
    unittest.main()
