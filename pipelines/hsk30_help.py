"""Resource lookup for the HSK 3.0 builder Help window."""

from __future__ import annotations

from pathlib import Path


GUIDE_RELATIVE_PATH = Path("docs") / "HSK3_VOCAB_ZIP_BUILDER_GUIDE.md"

FALLBACK_HELP_TEXT = """# HSK 3.0 Vocab ZIP Builder — Help

Không tìm thấy file hướng dẫn đầy đủ. Hãy mở:
docs/HSK3_VOCAB_ZIP_BUILDER_GUIDE.md

Quy trình: Build + Validate Local → Upload + Verify Packs → Publish Catalog + Signed Pointer.
Xem mục "Cách kích hoạt cấp độ mới trong app" để biết khi nào bấm Publish.

Các action cần xác nhận:
- Initialize Production Signing Key: INITIALIZE VOCAB SIGNING KEY
- Initialize VOCAB POINTER: INITIALIZE VOCAB POINTER
- Stage: STAGE <LEVEL> 3.0
- Publish: PUBLISH VOCAB CATALOG

Không đưa private seed vào log, output, chat hoặc Flutter. Chỉ current.json là
pointer mutable; ZIP, catalog revision và pointer archive là immutable. HSK 7–9
dùng canonical code hsk7_9. Xem file Markdown để biết đầy đủ về pack version,
compatibility hash, minAppBuild, pointerRevision và catalogRevision.
"""


def guide_path(anchor: str | Path | None = None) -> Path:
    """Resolve docs relative to source/app location, never process cwd."""
    if anchor is None:
        anchor_path = Path(__file__).resolve().parents[1]
    else:
        anchor_path = Path(anchor).expanduser().resolve()
        if anchor_path.is_file():
            anchor_path = anchor_path.parent
    return anchor_path / GUIDE_RELATIVE_PATH


def load_help_text(anchor: str | Path | None = None) -> tuple[str, Path, bool]:
    path = guide_path(anchor)
    try:
        return path.read_text(encoding="utf-8"), path, True
    except (OSError, UnicodeError):
        return FALLBACK_HELP_TEXT, path, False
