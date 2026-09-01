"""Tk 图形视图；所有业务命令均委托给共享控制器。"""

from __future__ import annotations

import contextlib
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

from .app.controller import CalibrationController
from .application import CaptureCandidate


class CalibrationGui:
    def __init__(self, root: tk.Tk, controller: CalibrationController) -> None:
        self.root = root
        self.controller = controller
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.photo: ImageTk.PhotoImage | None = None
        self.target_confirmed = False
        root.title("Handeye Toolkit")
        root.geometry("980x720")
        root.protocol("WM_DELETE_WINDOW", self._close)

        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="多相机 / Piper 手眼标定", font=("TkDefaultFont", 18, "bold")).pack(
            anchor="w"
        )
        self.status = tk.StringVar(value="请先阅读安全边界并连接只读采集源。")
        ttk.Label(outer, textvariable=self.status, wraplength=920).pack(anchor="w", pady=(8, 10))
        self.progress = tk.StringVar()
        ttk.Label(outer, textvariable=self.progress).pack(anchor="w")

        self.image = ttk.Label(outer, anchor="center")
        self.image.pack(fill="both", expand=True, pady=12)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")
        self.connect_button = ttk.Button(buttons, text="安全确认并连接", command=self._connect)
        self.assess_button = ttk.Button(buttons, text="检查姿态", command=self._assess, state="disabled")
        self.capture_button = ttk.Button(buttons, text="采集候选", command=self._capture, state="disabled")
        self.solve_button = ttk.Button(buttons, text="计算", command=self._solve, state="disabled")
        self.export_button = ttk.Button(buttons, text="导出", command=self._export, state="disabled")
        for button in (
            self.connect_button,
            self.assess_button,
            self.capture_button,
            self.solve_button,
            self.export_button,
        ):
            button.pack(side="left", padx=(0, 8))
        self._refresh()
        root.after(60, self._poll)

    def _refresh(self) -> None:
        snapshot = self.controller.snapshot
        self.progress.set(
            f"标定 {snapshot.calibration_count}/{snapshot.calibration_target} · "
            f"验证 {snapshot.validation_count}/{snapshot.validation_target} · "
            f"状态 {snapshot.state.value}"
        )
        connected = snapshot.hardware_connected
        collecting = self.controller.current_role() is not None
        self.assess_button.configure(state="normal" if connected and collecting else "disabled")
        self.capture_button.configure(state="normal" if connected and collecting else "disabled")
        ready = not collecting and snapshot.quality_passed is None
        self.solve_button.configure(state="normal" if ready else "disabled")
        self.export_button.configure(state="normal" if snapshot.quality_passed is True else "disabled")
        self.connect_button.configure(state="disabled" if connected else "normal")

    def _async(self, operation: Callable[[], Any], success: str) -> None:
        self.status.set("正在处理，请稍候……")
        for button in (
            self.connect_button,
            self.assess_button,
            self.capture_button,
            self.solve_button,
            self.export_button,
        ):
            button.configure(state="disabled")

        def worker() -> None:
            try:
                self.events.put((success, operation()))
            except BaseException as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _connect(self) -> None:
        accepted = messagebox.askyesno(
            "安全确认",
            "程序只读取相机图像和 Piper 状态/法兰反馈，不发送运动、使能、失能、"
            "复位、急停或夹爪命令。机械臂必须由现场人员手动移动。是否确认？",
        )
        if accepted:
            self._async(self.controller.acknowledge_and_connect, "connected")

    def _assess(self) -> None:
        self._async(self.controller.assess_pose, "assessed")

    def _capture(self) -> None:
        self._async(self.controller.capture, "candidate")

    def _show_candidate(self, candidate: CaptureCandidate) -> None:
        rgb = candidate.detection.overlay_bgr[:, :, ::-1]
        image = Image.fromarray(rgb)
        image.thumbnail((900, 500))
        self.photo = ImageTk.PhotoImage(image)
        self.image.configure(image=self.photo)
        if candidate.reasons:
            messagebox.showwarning("候选未通过", "；".join(candidate.reasons))
            self.controller.reject(candidate.candidate_id, "候选未通过自动质量检查")
            self.status.set("候选未保存，请按提示调整。")
            return
        if not self.target_confirmed:
            identity = candidate.detection.identity
            if identity is None or not messagebox.askyesno(
                "目标确认",
                f"期望：{identity.expected if identity else '未知'}\n"
                f"检测：{identity.observed if identity else '未知'}\n是否与现场实物一致？",
            ):
                self.controller.reject(candidate.candidate_id, "用户未确认目标身份")
                self.status.set("目标身份未确认，候选未保存。")
                return
            self.target_confirmed = True
        if messagebox.askyesno("保存候选", "图像和质量指标是否可接受？"):
            self.controller.accept(candidate.candidate_id, confirm_target=self.target_confirmed)
            self.status.set("样本已保存。")
        else:
            self.controller.reject(candidate.candidate_id, "GUI 用户拒绝候选")
            self.status.set("候选未保存。")

    def _solve(self) -> None:
        self._async(self.controller.solve, "solved")

    def _export(self) -> None:
        self._async(self.controller.export, "exported")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "error":
                    self.status.set("操作未完成。")
                    messagebox.showerror("操作未完成", str(payload))
                elif kind == "connected":
                    self.status.set("只读采集源已连接。")
                elif kind == "assessed":
                    assessment = payload
                    self.status.set(
                        "姿态检查："
                        + ("稳定" if assessment.stable else "不稳定")
                        + "；"
                        + ("有新意" if assessment.novel else "与已有样本重复")
                        + ("；" + "；".join(assessment.suggestions) if assessment.suggestions else "")
                    )
                elif kind == "candidate":
                    try:
                        self._show_candidate(payload)
                    except BaseException as exc:
                        candidate = payload
                        if isinstance(candidate, CaptureCandidate):
                            with contextlib.suppress(KeyError, RuntimeError, ValueError):
                                self.controller.reject(
                                    candidate.candidate_id,
                                    "GUI 处理候选时发生异常",
                                )
                        self.status.set("候选处理未完成。")
                        messagebox.showerror("候选处理未完成", str(exc))
                elif kind == "solved":
                    result = payload
                    self.status.set(
                        "计算完成："
                        + ("质量门禁通过。" if result.quality.passed else "质量门禁未通过。")
                    )
                    if not result.quality.passed:
                        messagebox.showwarning("质量门禁未通过", "；".join(result.quality.reasons))
                elif kind == "exported":
                    self.status.set(f"交付包已生成：{payload}")
                    messagebox.showinfo("导出完成", str(payload))
                self._refresh()
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _close(self) -> None:
        try:
            self.controller.close()
        finally:
            self.root.destroy()


def run_gui(controller: CalibrationController) -> int:
    root = tk.Tk()
    CalibrationGui(root, controller)
    root.mainloop()
    return 0


__all__ = ["CalibrationGui", "run_gui"]
