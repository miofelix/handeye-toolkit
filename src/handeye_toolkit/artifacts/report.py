"""本地审阅报告和脱敏交付报告。"""

from __future__ import annotations

import base64
import html
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from ..domain import (
    CalibrationPlan,
    CalibrationResult,
    RunRecord,
    SampleRecord,
    SynchronizedObservation,
)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _number(value: object, digits: int = 4) -> str:
    try:
        return f"{float(cast(Any, value)):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _matrix_rows(result: CalibrationResult) -> str:
    return "".join(
        "<tr>" + "".join(f"<td>{value:.9f}</td>" for value in row) + "</tr>"
        for row in result.transform.matrix
    )


def _summary_html(
    result: CalibrationResult,
    plan: CalibrationPlan,
    observations: Sequence[tuple[SampleRecord, SynchronizedObservation]],
    *,
    title: str,
    figures: str = "",
) -> str:
    quality = result.quality
    conclusion = "通过" if quality.passed else "未通过"
    reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in quality.reasons)
    if not reasons:
        reasons = "<li>所有质量门禁均通过</li>"
    included = [sample for sample, _ in observations if sample.included]
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Noto Sans CJK SC",sans-serif;max-width:1040px;margin:32px auto;padding:0 20px;color:#17202a}}
h1,h2{{color:#12344d}} .pass{{color:#147d3f}} .fail{{color:#b42318}} table{{border-collapse:collapse;width:100%;margin:12px 0}}
th,td{{border:1px solid #d8dee4;padding:7px;text-align:left}} code{{font-family:ui-monospace,monospace}} .matrix td{{text-align:right}}
.figures{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}} figure{{margin:0;border:1px solid #d8dee4;padding:10px}}
img{{max-width:100%;height:auto}} small{{color:#57606a}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="{'pass' if quality.passed else 'fail'}"><strong>质量结论：{conclusion}</strong></p>
<ul>{reasons}</ul>
<h2>标定结果</h2>
<p>模式：<code>{result.mode.value}</code>；变换约定：<code>{result.transform.parent_frame} &lt;- {result.transform.child_frame}</code>；方法：{html.escape(quality.method)}</p>
<table class="matrix">{_matrix_rows(result)}</table>
<h2>质量指标</h2>
<table><tr><th>标定样本</th><th>验证样本</th><th>验证平移 RMS</th><th>验证旋转 RMS</th><th>位置覆盖</th><th>旋转覆盖</th></tr>
<tr><td>{quality.sample_counts['calibration']}</td><td>{quality.sample_counts['validation']}</td>
<td>{_number(quality.validation_rms['translation_m'] * 1000, 3)} mm</td><td>{_number(quality.validation_rms['rotation_deg'], 3)}°</td>
<td>{_number(quality.coverage['position_span_m'], 3)} m</td><td>{_number(quality.coverage['rotation_span_deg'], 2)}°</td></tr></table>
<p>策略档案：<code>{html.escape(plan.profile)}</code>；已采用证据：{len(included)} 条。</p>
{figures}
<p><small>矩阵使用齐次坐标，所有内部平移单位为米。报告不构成机械臂运动指令。</small></p>
</body></html>"""


class HtmlReportRenderer:
    def render_local(
        self,
        *,
        run_path: Path,
        record: RunRecord,
        result: CalibrationResult,
        observations: Sequence[tuple[SampleRecord, SynchronizedObservation]],
    ) -> Path:
        figures: list[str] = []
        for sample, _ in observations:
            if not sample.included:
                continue
            image_path = run_path / "samples" / sample.sample_id / "overlay.png"
            if not image_path.is_file():
                continue
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            figures.append(
                f'<figure><img src="data:image/png;base64,{encoded}" alt="{html.escape(sample.sample_id)}">'
                f"<figcaption>{html.escape(sample.sample_id)} · {sample.role.value}</figcaption></figure>"
            )
        figure_section = (
            '<h2>本地样本图像</h2><div class="figures">' + "".join(figures) + "</div>"
            if figures
            else ""
        )
        payload = _summary_html(
            result,
            record.plan,
            observations,
            title=f"Handeye Toolkit 本地报告 · {record.run_id}",
            figures=figure_section,
        )
        path = run_path / "report.local.html"
        _atomic_bytes(path, payload.encode("utf-8"))
        return path

    def render_sanitized(
        self,
        *,
        result: CalibrationResult,
        plan: CalibrationPlan,
        observations: Sequence[tuple[SampleRecord, SynchronizedObservation]],
    ) -> bytes:
        payload = _summary_html(
            result,
            plan,
            observations,
            title="Handeye Toolkit 脱敏标定报告",
        )
        return payload.encode("utf-8")


__all__ = ["HtmlReportRenderer"]
