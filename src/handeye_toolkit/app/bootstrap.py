"""产品组合根：这里是业务 YAML 与通用应用 API 的唯一连接点。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..adapters import DabaiPiperRigFactory, FileRunRepository
from ..algorithms.solver import OpenCvHandeyeSolver
from ..application import CalibrationRun
from ..artifacts import CalibrationArtifactExporter, HtmlReportRenderer
from ..ports import EventSink
from .config import DEFAULT_CONFIG_PATH, ProductConfig, load_product_config


def create_product_run(
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    config: ProductConfig | None = None,
    can_channel: str | None = None,
    output_root: str | Path = "runs",
    event_sink: EventSink | None = None,
) -> CalibrationRun:
    selected = config if config is not None else load_product_config(config_path)
    if can_channel is not None:
        selected = replace(selected, piper_can_channel=can_channel)
    repository = FileRunRepository()
    renderer = HtmlReportRenderer()
    return CalibrationRun.create(
        plan=selected.plan,
        factory=DabaiPiperRigFactory(selected.acquisition),
        repository=repository,
        solver=OpenCvHandeyeSolver(),
        exporter=CalibrationArtifactExporter(renderer),
        reporter=renderer,
        output_root=output_root,
        event_sink=event_sink,
    )


def resume_product_run(
    run_ref: str | Path,
    *,
    event_sink: EventSink | None = None,
) -> CalibrationRun:
    repository = FileRunRepository()
    _, record = repository.load(run_ref)
    renderer = HtmlReportRenderer()
    return CalibrationRun.resume(
        run_ref,
        factory=DabaiPiperRigFactory(record.acquisition),
        repository=repository,
        solver=OpenCvHandeyeSolver(),
        exporter=CalibrationArtifactExporter(renderer),
        reporter=renderer,
        event_sink=event_sink,
    )


__all__ = ["create_product_run", "resume_product_run"]
