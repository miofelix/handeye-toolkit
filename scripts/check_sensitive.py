"""扫描 Git 跟踪文件中不应提交的硬件信息和敏感制品。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
FORBIDDEN_SUFFIXES = {
    ".bag",
    ".db",
    ".jpeg",
    ".jpg",
    ".key",
    ".npy",
    ".npz",
    ".pem",
    ".png",
    ".zip",
}
TEXT_PATTERNS = {
    "个人绝对路径": re.compile(r"/(?:Users|home)/[^/\s]+/"),
    "私钥内容": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "常见访问令牌": re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"),
    "URL 内嵌凭据": re.compile(r"[a-z][a-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@", re.IGNORECASE),
}
CONFIG_VALUE_PATTERN = re.compile(
    r"^\s*(?:serial_number|frame_id|camera_frame|base_frame|flange_frame|can_channel)"
    r"\s*:\s*(?!null\s*(?:#.*)?$)\S+",
    re.MULTILINE,
)


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def scan() -> list[str]:
    findings: list[str] = []
    for path in tracked_files():
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if path == SELF:
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(f"禁止提交的文件类型：{relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"无法审核的二进制文件：{relative}")
            continue
        for label, pattern in TEXT_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{label}：{relative}")
        if (
            "hardware" in relative.name
            and relative.suffix in {".yaml", ".yml"}
            and CONFIG_VALUE_PATTERN.search(text)
        ):
            findings.append(f"硬件模板包含已填写的身份或映射：{relative}")
    return findings


def main() -> int:
    findings = scan()
    if findings:
        print("敏感信息扫描失败：", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print("敏感信息扫描通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
