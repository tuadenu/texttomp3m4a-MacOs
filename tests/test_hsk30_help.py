import os
import tempfile
import unittest
from pathlib import Path

from pipelines.hsk30_help import FALLBACK_HELP_TEXT, GUIDE_RELATIVE_PATH, guide_path, load_help_text


class Hsk30HelpTests(unittest.TestCase):
    def test_loader_resolves_repo_docs_independent_of_cwd(self):
        repo = Path(__file__).resolve().parents[1]
        original = Path.cwd()
        try:
            os.chdir(tempfile.gettempdir())
            text, path, loaded = load_help_text(repo)
        finally:
            os.chdir(original)
        self.assertTrue(loaded)
        self.assertEqual(repo / GUIDE_RELATIVE_PATH, path)
        required = (
            "Build + Validate Local", "Upload + Verify Packs", "Publish Catalog + Signed Pointer",
            "Cách kích hoạt cấp độ mới trong app", "REMOTE PACKS VERIFIED", "SIGNING KEY READY",
            "POINTER ACTIVE", "PUBLISH VOCAB CATALOG", "quét tất cả deploy receipt",
            "Initialize Production Signing Key", "Initialize VOCAB POINTER", "private seed",
            "current.json", "pointerRevision", "catalogRevision", "hsk7_9", "minAppBuild",
            "Refresh Pointer Status", "POINTER STATUS UNKNOWN", "HSK2 sẽ tạo catalog từ 14 entry",
        )
        for phrase in required:
            self.assertIn(phrase, text)

    def test_missing_markdown_uses_non_crashing_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            text, path, loaded = load_help_text(temp)
        self.assertFalse(loaded)
        self.assertEqual(FALLBACK_HELP_TEXT, text)
        self.assertIn("docs/HSK3_VOCAB_ZIP_BUILDER_GUIDE.md", text)
        self.assertIn("Cách kích hoạt cấp độ mới trong app", text)

    def test_guide_path_does_not_depend_on_process_cwd(self):
        self.assertTrue(guide_path().is_absolute())
        self.assertTrue(guide_path().name == "HSK3_VOCAB_ZIP_BUILDER_GUIDE.md")


if __name__ == "__main__":
    unittest.main()
