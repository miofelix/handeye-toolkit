"""交付制品的固定合同和安全上限。"""

from __future__ import annotations

ARTIFACT_TYPE = "handeye-calibration"
ARTIFACT_MEMBERS = ("manifest.json", "result.json", "evidence.json", "report.html")
HASHED_MEMBERS = ("result.json", "evidence.json", "report.html")
MEMBER_MEDIA_TYPES = {
    "result.json": "application/json",
    "evidence.json": "application/json",
    "report.html": "text/html; charset=utf-8",
}
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 128 * 1024 * 1024

__all__ = [
    "ARTIFACT_MEMBERS",
    "ARTIFACT_TYPE",
    "HASHED_MEMBERS",
    "MAX_ARCHIVE_BYTES",
    "MAX_MEMBER_BYTES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "MEMBER_MEDIA_TYPES",
]
