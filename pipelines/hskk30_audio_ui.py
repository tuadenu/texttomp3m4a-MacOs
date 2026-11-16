"""HSKK3-only Tk UI, separate from the HSK listening builder."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict

from pipelines.hskk30_audio import (
    BASE_DIR,
    HSKK3_ACTIVE_LEVELS,
    HSKK3_DEFAULT_ANSWER_SOURCE,
    HSKK3_DEFAULT_ASSET_ZIP,
    HSKK3_DEFAULT_EXAM_SOURCE,
    HSKK3_DEFAULT_SAMPLE_AUDIO,
    HSKK3_DEFAULT_SAMPLE_PDF,
    HSKK3_DEFAULT_SAMPLE_TRANSCRIPT,
    HSKK3_LEVELS,
    build_hskk3_golden,
    format_hskk3_validation_report,
    inspect_hskk3_asset_archive,
    inspect_hskk3_reference_documents,
    validate_hskk3_workbooks,
)


def _selected_voice(context: Dict[str, Any]) -> str:
    engine = str(context.get("engine", "")).strip().casefold()
    if engine == "polly":
        return "Zhiyu"
    gender = str(context.get("voice", "")).strip()
    if gender in {"Nam", "Male"}:
        return str(context.get("zh_male_voice") or gender)
    if gender in {"Nữ", "Female"}:
        return str(context.get("zh_female_voice") or gender)
    return str(context.get("zh_narrator_voice") or "")


def open_hskk30_audio_popup(
    parent,
    set_popup_icon: Callable,
    get_tts_context: Callable[[], Dict[str, Any]],
    tts_provider: Callable[..., Any],
) -> None:
    window = tk.Toplevel(parent)
    set_popup_icon(window)
    window.title("HSKK 3.0 – Khẩu ngữ")
    window.geometry("1120x850")
    window.minsize(980, 720)
    window.transient(parent)
    window.grab_set()

    level_var = tk.StringVar(value="HSK3")
    mode_var = tk.StringVar(value="both")
    exam_var = tk.StringVar(value="001")
    exam_path_var = tk.StringVar(value=str(HSKK3_DEFAULT_EXAM_SOURCE))
    answer_path_var = tk.StringVar(value=str(HSKK3_DEFAULT_ANSWER_SOURCE))
    asset_zip_var = tk.StringVar(value=str(HSKK3_DEFAULT_ASSET_ZIP))
    sample_audio_var = tk.StringVar(value=str(HSKK3_DEFAULT_SAMPLE_AUDIO))
    sample_pdf_var = tk.StringVar(value=str(HSKK3_DEFAULT_SAMPLE_PDF))
    transcript_var = tk.StringVar(value=str(HSKK3_DEFAULT_SAMPLE_TRANSCRIPT))
    output_var = tk.StringVar(value=str(BASE_DIR / "output" / "hskk3"))
    speed_var = tk.StringVar(value="1.00")
    bitrate_var = tk.StringVar(value="96")
    engine_var = tk.StringVar(value="")
    voice_var = tk.StringVar(value="")
    status_var = tk.StringVar(value="Đang đọc workbook và fixed assets…")
    progress_var = tk.DoubleVar(value=0)
    validation_state: Dict[str, Any] = {"workbooks": None, "assets": None, "references": None, "passed": False}
    preview_text = None
    log_text = None
    progressbar = None
    validate_button = None
    golden_button = None

    try:
        context = get_tts_context()
    except Exception:
        context = {}
    engine_var.set(str(context.get("engine") or ""))
    voice_var.set(_selected_voice(context))

    def append_log(line: str, *, error: bool = False) -> None:
        if log_text is not None:
            def write_ui():
                log_text.configure(state="normal")
                log_text.insert(tk.END, line.rstrip() + "\n")
                log_text.see(tk.END)
                log_text.configure(state="disabled")
            window.after(0, write_ui)
        if error:
            error_log = BASE_DIR / "AppData" / "loi_hskk_audio.txt"
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with error_log.open("a", encoding="utf-8") as stream:
                stream.write(line.rstrip() + "\n")

    def set_preview(content: str) -> None:
        if preview_text is None:
            return
        preview_text.configure(state="normal")
        preview_text.delete("1.0", tk.END)
        preview_text.insert("1.0", content)
        preview_text.configure(state="disabled")

    def update_status(content: str) -> None:
        window.after(0, lambda: status_var.set(content))

    def run_validation() -> None:
        if validate_button is not None:
            validate_button.configure(state="disabled")
        if golden_button is not None:
            golden_button.configure(state="disabled")
        status_var.set("Đang parse 30 đề và kiểm tra ZIP/MP3…")
        progress_var.set(0)

        def worker():
            try:
                workbook_report = validate_hskk3_workbooks(
                    exam_path_var.get().strip(), answer_path_var.get().strip()
                )
                asset_report = inspect_hskk3_asset_archive(
                    asset_zip_var.get().strip(), sample_audio_var.get().strip()
                )
                reference_report = inspect_hskk3_reference_documents(
                    sample_pdf_var.get().strip(), transcript_var.get().strip()
                )
                asset_issues = []
                if not asset_report.get("inventory_ok") or asset_report.get("zip_test") != "PASS":
                    asset_issues.append("ZIP CRC hoặc inventory manifest/README/audio chưa PASS.")
                if not asset_report.get("required_filenames_ok"):
                    asset_issues.append("Tên 19 fixed assets không khớp inventory đã audit.")
                if not asset_report.get("asset_hashes_ok"):
                    asset_issues.append("SHA256 asset không khớp manifest.")
                if not asset_report.get("asset_durations_ok"):
                    asset_issues.append("Thời lượng cut không khớp manifest.")
                if not asset_report.get("source_audio_hash_matches_manifest"):
                    asset_issues.append("MP3 mẫu không khớp source SHA256 trong manifest.")
                if asset_report.get("sha256") != "9846170ec6966e84b384a4f3eefc929a3b8330ed734b16ae7b90f630bbf0dbb6":
                    asset_issues.append("ZIP fixed assets đã thay đổi so với bộ REVIEW đã audit.")
                if asset_report.get("source_audio_sha256") != "844271e97474431fdf9bf77e3321c6891010a313f3466017d483f84ed55ab152":
                    asset_issues.append("MP3 mẫu đã thay đổi so với nguồn đã audit.")
                if asset_report.get("manifest_status") != "PROVISIONAL_AUDIT_ONLY_NOT_APPROVED_FOR_BATCH":
                    asset_issues.append("Trạng thái manifest khác bộ REVIEW đang được audit.")
                if not reference_report.get("pdf_sha256_matches_reviewed"):
                    asset_issues.append("PDF mẫu đã thay đổi so với tài liệu đã đối chiếu.")
                if not reference_report.get("transcript_sha256_matches_reviewed"):
                    asset_issues.append("Transcript đã thay đổi so với tài liệu đã đối chiếu.")
                validation_state["workbooks"] = workbook_report
                validation_state["assets"] = asset_report
                validation_state["references"] = reference_report
                passed = workbook_report.get("status") == "PASS" and not asset_issues
                validation_state["passed"] = passed
                review_lines = [
                    "",
                    "Fixed assets review",
                    f"ZIP CRC: {asset_report.get('zip_test')}",
                    f"Audio inventory: {asset_report.get('audio_count')}/19 · tên file chuẩn: {asset_report.get('required_filenames_ok')}",
                    f"Asset SHA256/ranges: {asset_report.get('asset_hashes_ok')} / {asset_report.get('asset_durations_ok')}",
                    f"MP3 mẫu SHA256: {asset_report.get('source_audio_sha256')}",
                    f"MP3 khớp manifest: {asset_report.get('source_audio_hash_matches_manifest')}",
                    f"Trạng thái manifest: {asset_report.get('manifest_status')}",
                    "Hai clip REVIEW: ASR cục bộ không nhận diện câu hỏi riêng của đề mẫu.",
                    "Ranh giới fixed assets vẫn cần bạn nghe duyệt trước khi mở batch production.",
                    f"README/manifest: {', '.join(asset_report.get('readme_files', []) + asset_report.get('manifest_files', []))}",
                    f"Sample PDF: {sample_pdf_var.get()}",
                    f"Sample PDF SHA256: {reference_report.get('pdf_sha256')}",
                    f"Transcript DOCX SHA256: {reference_report.get('transcript_sha256')}",
                    "Part 3: PDF/transcript có câu hỏi trên giấy; ASR cục bộ không nhận diện câu hỏi trong MP3 mẫu → read_part3_questions=False.",
                    f"Engine hiện tại: {engine_var.get()} · voice: {voice_var.get()}",
                    "Chỉ xuất thử đề 001; production batch luôn khóa.",
                ]
                if asset_issues:
                    review_lines.extend(["", "Lỗi fixed assets:", *[f"- {item}" for item in asset_issues]])
                if workbook_report.get("status") == "FAIL":
                    review_lines.extend(["", *workbook_report.get("issues", [])])
                window.after(0, lambda: set_preview(format_hskk3_validation_report(workbook_report) + "\n" + "\n".join(review_lines)))
                update_status(
                    "Validation PASS · parser-only, 0 TTS" if passed
                    else "Validation FAIL · golden bị khóa"
                )
                append_log("HSKK3 validation " + ("PASS" if passed else "FAIL"))
                if validate_button is not None:
                    window.after(0, lambda: validate_button.configure(state="normal"))
                if golden_button is not None:
                    window.after(0, lambda: golden_button.configure(state="normal" if passed else "disabled"))
            except Exception as exc:
                validation_state["workbooks"] = None
                validation_state["assets"] = None
                validation_state["references"] = None
                validation_state["passed"] = False
                window.after(0, lambda exc=exc: set_preview(f"Validation error:\n{exc}"))
                update_status(f"Validation error: {exc}")
                append_log(f"Validation error: {exc}", error=True)
                if validate_button is not None:
                    window.after(0, lambda: validate_button.configure(state="normal"))
                if golden_button is not None:
                    window.after(0, lambda: golden_button.configure(state="disabled"))

        threading.Thread(target=worker, daemon=True).start()

    def choose_file(target: tk.StringVar, title: str, types) -> None:
        current = target.get().strip()
        selected = filedialog.askopenfilename(
            parent=window,
            title=title,
            initialdir=str(Path(current).parent) if current else str(BASE_DIR),
            initialfile=Path(current).name if current else "",
            filetypes=types,
        )
        if selected:
            target.set(selected)
            validation_state["workbooks"] = None
            validation_state["assets"] = None
            validation_state["references"] = None
            validation_state["passed"] = False
            if golden_button is not None:
                golden_button.configure(state="disabled")

    def choose_output() -> None:
        selected = filedialog.askdirectory(
            parent=window,
            title="Chọn thư mục output HSKK3",
            initialdir=output_var.get() or str(BASE_DIR),
            mustexist=False,
        )
        if selected:
            output_var.set(selected)

    def run_golden() -> None:
        workbook_report = validation_state.get("workbooks")
        asset_report = validation_state.get("assets")
        reference_report = validation_state.get("references")
        if (not validation_state.get("passed") or workbook_report is None
                or workbook_report.get("status") != "PASS" or asset_report is None or reference_report is None):
            messagebox.showerror("HSKK3", "Hãy chạy validation và cần PASS trước khi xuất golden.", parent=window)
            return
        mode = mode_var.get()
        export_exam = mode in {"exam", "both"}
        export_answers = mode in {"answers", "both"}
        try:
            speed = float(speed_var.get())
            bitrate = int(bitrate_var.get())
        except ValueError:
            messagebox.showerror("HSKK3", "Speed hoặc bitrate không hợp lệ.", parent=window)
            return
        if exam_var.get().strip() != "001":
            messagebox.showerror("HSKK3", "Giai đoạn này chỉ xuất đề 001.", parent=window)
            return
        engine = engine_var.get().strip()
        voice = voice_var.get().strip()
        if not engine or not voice:
            messagebox.showerror("HSKK3", "Thiếu engine hoặc voice hiện tại.", parent=window)
            return
        if golden_button is not None:
            golden_button.configure(state="disabled")
        if validate_button is not None:
            validate_button.configure(state="disabled")
        progress_var.set(0)
        status_var.set("Đang xuất golden 001…")
        append_log(f"Bắt đầu golden HSKK3-001 · {mode} · {engine} · {voice} · {speed:.2f}x · {bitrate} kbps")

        progress_state = {"count": 0}
        total_steps = (15 if export_answers else 0) + (8 if export_exam else 0) + (2 if export_exam else 1)

        def progress(message: str) -> None:
            progress_state["count"] += 1
            percent = min(98, round(progress_state["count"] * 98 / max(1, total_steps)))
            update_status(message)
            append_log(message)
            window.after(0, lambda percent=percent: progress_var.set(percent))

        def worker():
            try:
                report = build_hskk3_golden(
                    workbook_report,
                    asset_zip_var.get().strip(),
                    sample_audio_var.get().strip(),
                    output_var.get().strip(),
                    engine=engine,
                    voice=voice,
                    speed=speed,
                    bitrate_kbps=bitrate,
                    tts_provider=tts_provider,
                    export_exam=export_exam,
                    export_answers=export_answers,
                    read_part3_questions=False,
                    sample_pdf_path=sample_pdf_var.get().strip(),
                    sample_transcript_path=transcript_var.get().strip(),
                    progress_callback=progress,
                )
                files = []
                if report.get("exam_audio"):
                    files.append(report["exam_audio"]["output_file"])
                if report.get("answer_audio"):
                    files.extend(report["answer_audio"]["output_files"])
                message = "Golden 001 hoàn tất:\n" + "\n".join(files) + f"\n\nTimeline/report: {report['report_file']}"
                append_log("Golden 001 completed")
                window.after(0, lambda: status_var.set("Golden 001 hoàn tất; không có batch."))
                window.after(0, lambda: set_preview(message))
                window.after(0, lambda: progress_var.set(100))
                window.after(0, lambda: messagebox.showinfo("HSKK3 golden", message, parent=window))
            except Exception as exc:
                append_log(f"Golden error: {exc}", error=True)
                window.after(0, lambda exc=exc: status_var.set(f"Golden error: {exc}"))
                window.after(0, lambda exc=exc: set_preview(f"Golden error:\n{exc}\n\nXem AppData/loi_hskk_audio.txt."))
                window.after(0, lambda exc=exc: messagebox.showerror("HSKK3 golden", str(exc), parent=window))
            finally:
                if golden_button is not None:
                    window.after(0, lambda: golden_button.configure(
                        state="normal" if validation_state.get("workbooks", {}).get("status") == "PASS" else "disabled"
                    ))
                if validate_button is not None:
                    window.after(0, lambda: validate_button.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)
    outer = ttk.Frame(window, padding=12)
    outer.grid(row=0, column=0, sticky="nsew")
    outer.columnconfigure(1, weight=1)
    outer.rowconfigure(12, weight=1)

    ttk.Label(outer, text="HSKK 3.0 – Khẩu ngữ", font=("Arial", 16, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
    ttk.Label(outer, text="Cấp:").grid(row=1, column=0, sticky="w")
    level_frame = ttk.Frame(outer)
    level_frame.grid(row=1, column=1, columnspan=2, sticky="w")
    for level in HSKK3_LEVELS:
        ttk.Radiobutton(
            level_frame, text=level, variable=level_var, value=level,
            state="normal" if level in HSKK3_ACTIVE_LEVELS else "disabled",
        ).pack(side="left", padx=(0, 14))

    def file_row(row: int, label: str, variable: tk.StringVar, title: str, types) -> None:
        ttk.Label(outer, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(outer, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(outer, text="Chọn…", command=lambda: choose_file(variable, title, types)).grid(row=row, column=2, sticky="ew", pady=2)

    xlsx_types = [("Excel", "*.xlsx *.xlsm")]
    file_row(2, "Excel đề thi", exam_path_var, "Chọn Excel đề thi HSKK3", xlsx_types)
    file_row(3, "Excel đáp án", answer_path_var, "Chọn Excel đáp án HSKK3", xlsx_types)
    file_row(4, "Fixed assets ZIP", asset_zip_var, "Chọn ZIP fixed assets HSKK3", [("ZIP", "*.zip")])
    file_row(5, "MP3 mẫu nguồn", sample_audio_var, "Chọn MP3 mẫu chính thức", [("Audio", "*.mp3 *.m4a")])
    file_row(6, "PDF mẫu tham chiếu", sample_pdf_var, "Chọn PDF mẫu", [("PDF", "*.pdf")])
    file_row(7, "Transcript DOCX", transcript_var, "Chọn transcript DOCX", [("Word", "*.docx")])
    ttk.Label(outer, text="Thư mục output").grid(row=8, column=0, sticky="w", pady=2)
    ttk.Entry(outer, textvariable=output_var).grid(row=8, column=1, sticky="ew", padx=6, pady=2)
    ttk.Button(outer, text="Chọn…", command=choose_output).grid(row=8, column=2, sticky="ew", pady=2)

    settings = ttk.LabelFrame(outer, text="Cấu hình xuất", padding=8)
    settings.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(8, 4))
    settings.columnconfigure(1, weight=1)
    ttk.Label(settings, text="Chế độ").grid(row=0, column=0, sticky="w")
    mode_frame = ttk.Frame(settings)
    mode_frame.grid(row=0, column=1, columnspan=3, sticky="w")
    for label, value in (("Audio đề thi", "exam"), ("Audio đáp án", "answers"), ("Cả đề thi và đáp án", "both")):
        ttk.Radiobutton(mode_frame, text=label, variable=mode_var, value=value).pack(side="left", padx=(0, 12))
    ttk.Label(settings, text="Đề").grid(row=1, column=0, sticky="w", pady=(6, 0))
    exam_combo = ttk.Combobox(settings, textvariable=exam_var, values=("001",), state="readonly", width=8)
    exam_combo.grid(row=1, column=1, sticky="w", pady=(6, 0))
    ttk.Label(settings, text="Speed").grid(row=1, column=2, sticky="e", padx=(12, 4), pady=(6, 0))
    ttk.Spinbox(settings, textvariable=speed_var, from_=0.50, to=2.00, increment=0.05, width=8).grid(row=1, column=3, sticky="w", pady=(6, 0))
    ttk.Label(settings, text="Engine hiện tại").grid(row=2, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(settings, textvariable=engine_var, state="readonly").grid(row=2, column=1, sticky="ew", pady=(6, 0))
    ttk.Label(settings, text="Voice hiện tại").grid(row=2, column=2, sticky="e", padx=(12, 4), pady=(6, 0))
    ttk.Entry(settings, textvariable=voice_var).grid(row=2, column=3, sticky="ew", pady=(6, 0))
    ttk.Label(settings, text="Bitrate AAC").grid(row=3, column=0, sticky="w", pady=(6, 0))
    ttk.Combobox(settings, textvariable=bitrate_var, values=("64", "96", "128", "160", "192"), state="readonly", width=8).grid(row=3, column=1, sticky="w", pady=(6, 0))
    ttk.Label(settings, text="Part 3 questions: không đọc (đối chiếu MP3/PDF)").grid(row=3, column=2, columnspan=2, sticky="w", pady=(6, 0))

    controls = ttk.Frame(outer)
    controls.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(6, 4))
    validate_button = ttk.Button(controls, text="Validation + Preview 30 đề", command=run_validation)
    validate_button.pack(side="left")
    golden_button = ttk.Button(controls, text="Xuất golden đề 001", command=run_golden, state="disabled")
    golden_button.pack(side="left", padx=8)
    ttk.Label(controls, textvariable=status_var).pack(side="left", padx=8)
    progressbar = ttk.Progressbar(outer, variable=progress_var, maximum=100, mode="determinate")
    progressbar.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(2, 6))

    panels = ttk.Panedwindow(outer, orient="vertical")
    panels.grid(row=12, column=0, columnspan=3, sticky="nsew")
    preview_frame = ttk.LabelFrame(panels, text="Preview dữ liệu, validation và report")
    log_frame = ttk.LabelFrame(panels, text="Log tiến trình / lỗi")
    preview_text = tk.Text(preview_frame, wrap="word", height=18, state="disabled")
    preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=preview_text.yview)
    preview_text.configure(yscrollcommand=preview_scroll.set)
    preview_text.pack(side="left", fill="both", expand=True)
    preview_scroll.pack(side="right", fill="y")
    log_text = tk.Text(log_frame, wrap="word", height=6, state="disabled")
    log_text.pack(fill="both", expand=True)
    panels.add(preview_frame, weight=4)
    panels.add(log_frame, weight=1)

    window.after(200, run_validation)
