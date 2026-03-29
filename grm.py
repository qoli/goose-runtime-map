#!/usr/bin/env python3
"""Run Goose runtime-map prompts and view saved answers."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

MAX_RETRIES = 5
SKELETON_THRESHOLD = 0.70
VALIDATION_FAILED_EXIT_CODE = 3

PROMPT_TEMPLATE = """你現在不是在回答一般程式問題，也不是在做 bug triage。
你正在為一個程式碼庫生成一份「Codemap artifact」。

目標：
建立一份可以讓人類與 AI 共享理解的 code map，用來說明某個功能、流程、子系統或問題域，在整個 repo 中是如何被組成、如何流動、以及如何落地的。

輸入問題：
{question}

你的首要任務不是找單一答案，而是建立一張「可導航的理解地圖」。
這份地圖必須能回答：
- 這個能力/流程的核心角色是誰？
- 它們如何協作？
- 執行順序是什麼？
- 狀態與資料如何流動？
- 副作用在哪裡發生？
- 有哪些主要分支、變體、回退、修復路徑？
- 哪些部分是核心結構，哪些只是包裝、轉發、或殘留代碼？

請遵守以下原則：

一、先定義 map scope
- 先判斷這個問題要建立的是哪一種 map：
  1. feature map：某個功能如何運作
  2. flow map：某個流程如何被觸發並執行
  3. subsystem map：某個子系統的責任邊界與交互
  4. runtime map：某個運行時現象如何形成
  5. state/effect map：某個狀態或副作用的來源與落點
- 先明確寫出這次 map 的 scope，避免失控泛化成 repo 導覽。

二、先建立結構，再補細節
- 先找出最重要的：
  - top-level entrypoints
  - orchestrators / coordinators
  - core workers / executors
  - state owners
  - side-effect boundaries
  - external boundaries (network/db/ui/process/tooling)
- 先給出骨架，再展開局部。

三、輸出的是「分層結構」，不是平鋪檔案清單
你要優先建立：
- Layer / role
- Node / responsibility
- Edge / trigger / call / state flow / effect flow
- Boundary / ownership
而不是只列：
- 哪些檔案
- 哪些函數

四、必須區分不同類型的關係
對每條關係，盡量標示它是：
- calls
- triggers
- reads
- writes
- transforms
- persists
- renders
- synchronizes
- retries
- reconciles
- cancels / cleans up

五、必須區分不同類型的節點
對每個關鍵節點，說明：
- role
- why it exists
- upstream inputs
- downstream outputs
- owned state
- side effects
- lifecycle relevance
- whether it is core / wrapper / adapter / dead / partial

六、優先建立「主理解路徑」
先建立最能幫助人理解系統的路徑，而不是一開始就窮舉所有細枝末節。
但如果問題本身要求所有入口、所有來源、所有寫入點，再在主地圖之後追加 exhaustive appendix。

七、不要把「可見代碼」與「合理推論」混在一起
- 可直接從代碼確認的內容，正常描述
- 推論內容標為 inference
- 無法確認是否真接通的路徑標為 unverified
- 看起來殘留或半接通的部分標為 dead/partial candidate

八、關注「人類真正需要的理解」
你的輸出應讓讀者能快速回答：
- 我要改這個功能，應該先看哪裡？
- 我要 debug 這個流程，應該沿哪條主路徑追？
- 我要重構，責任邊界在哪裡？
- 我該警惕哪些 hidden coupling / duplicated responsibility / stale path？

九、避免兩個極端
不要變成：
- repo tour（太泛）
- bug trace only（太窄）

而要維持在：
- codebase mental model artifact

十、A 段硬約束（不可省略）
- 在 A 段必須明確說明「是否選用 runtime map」以及理由。
- 若不選 runtime map，也必須寫出不選的原因，不能只給 map 名稱。

輸出格式（中文）：

A. Map type and scope
- 這次建立的是哪一種 codemap
- 是否選用 runtime map，以及選/不選的理由（必填）
- map 的範圍與不包含的範圍

B. System summary
- 用少量文字說清楚這個能力/流程/子系統的本質

C. Top-level structure
- 用分層方式列出主要角色與責任
- 例如：UI layer / orchestration layer / domain layer / execution layer / effect boundary

D. Key nodes
對每個重要節點列出：
- name
- role
- why it matters
- owned state / responsibility
- upstream
- downstream
- side effects
- classification（core / adapter / wrapper / dead / partial）

E. Main execution / understanding paths
- 按「最重要的理解路徑」描述 1~3 條主路徑
- 每條路徑要說明 trigger → coordination → work → effect

F. State and data movement
- 哪些 state 在哪裡擁有
- 哪些資料在跨層流動
- 哪些轉換點最關鍵

G. Boundaries and side effects
- UI render boundary
- persistence boundary
- network/tool/process boundary
- async/task boundary
- cancellation/cleanup boundary

H. Variants and special paths
- alternative paths
- fallback paths
- repair/reconcile paths
- error/cancel/cleanup paths

I. Design observations
- 命名誤導
- 責任重疊
- hidden coupling
- dead/partial path
- 架構上值得警惕的點

J. Open questions / uncertainties
- inference
- unverified path
- 缺乏證據的部分

K. Optional appendix: exhaustive entry/source/write list
- 只有當問題明確要求「所有入口 / 所有來源 / 所有寫入點」時才輸出
"""


@dataclass
class HistoryEntry:
    path: Path
    timestamp: str
    question: str


@dataclass
class ValidationResult:
    passed: bool
    ratio: float
    matched_sections: int
    total_sections: int
    reasons: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Goose runtime-map wrapper with history viewing and glow rendering."
    )
    parser.add_argument("question_pos", nargs="*", help="Question as positional text")
    parser.add_argument("-q", "--question", help="Question text")
    parser.add_argument("--history", action="store_true", help="Open history selector (read-only)")
    parser.add_argument(
        "--status",
        choices=("stream", "stage", "min"),
        default="stream",
        help="Runtime status verbosity",
    )
    parser.add_argument("--width", type=int, default=120, help="glow render width")
    parser.add_argument("--no-glow", action="store_true", help="Print markdown without glow")
    parser.add_argument("--goose-bin", default=os.environ.get("GOOSE_BIN", "goose"))
    parser.add_argument("--glow-bin", default=os.environ.get("GLOW_BIN", "glow"))
    return parser.parse_args()


def print_status(message: str, level: str, mode: str) -> None:
    if mode == "min" and level != "error":
        return
    if mode == "stage" and level == "stream":
        return
    print(message, file=sys.stderr)


def slugify(text: str, max_len: int = 64) -> str:
    cleaned = re.sub(r"\s+", "-", text.strip())
    cleaned = re.sub(r"[^\w\-.]+", "", cleaned)
    cleaned = cleaned.strip("-._")
    if not cleaned:
        return "question"
    return cleaned[:max_len]


def extract_field(prefix: str, text: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def get_history_dir() -> Path:
    return Path.cwd() / ".grm-history"


def list_history_entries() -> list[HistoryEntry]:
    history_dir = get_history_dir()
    if not history_dir.exists():
        return []

    entries: list[HistoryEntry] = []
    try:
        history_paths = sorted(history_dir.glob("*.md"), reverse=True)
    except OSError as exc:
        raise ValueError(f"unable to read history directory '{history_dir}': {exc}") from exc

    for path in history_paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        timestamp = extract_field("- timestamp: ", text) or path.stem.split("-", 2)[0]
        question = extract_field("- question: ", text) or "(no question metadata)"
        entries.append(HistoryEntry(path=path, timestamp=timestamp, question=question))
    return entries


def choose_with_fzf(entries: list[HistoryEntry]) -> HistoryEntry | None:
    if shutil.which("fzf") is None:
        return None

    lines = [
        f"{idx}\t{entry.timestamp}\t{entry.question}\t{entry.path}"
        for idx, entry in enumerate(entries, start=1)
    ]
    proc = subprocess.run(
        ["fzf", "--prompt", "history> ", "--delimiter", "\t", "--with-nth", "2,3"],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    selected = proc.stdout.strip()
    if not selected:
        return None
    parts = selected.split("\t")
    if len(parts) < 4:
        return None
    selected_path = Path(parts[3])
    for entry in entries:
        if entry.path == selected_path:
            return entry
    return None


def choose_with_numbered_list(entries: list[HistoryEntry]) -> HistoryEntry | None:
    print("History entries:", file=sys.stderr)
    for idx, entry in enumerate(entries, start=1):
        print(f"  {idx:>2}. {entry.timestamp} | {entry.question}", file=sys.stderr)
    print("Choose entry number (empty to cancel): ", end="", file=sys.stderr, flush=True)
    raw = input().strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError("invalid selection")
    idx = int(raw)
    if idx < 1 or idx > len(entries):
        raise ValueError("selection out of range")
    return entries[idx - 1]


def pick_history_entry(entries: list[HistoryEntry], mode: str) -> HistoryEntry | None:
    picked = choose_with_fzf(entries)
    if picked:
        return picked
    print_status("[status] fzf unavailable/cancelled; falling back to numbered list", "stage", mode)
    return choose_with_numbered_list(entries)


def render_markdown(path: Path, glow_bin: str, width: int, no_glow: bool) -> int:
    if no_glow:
        print(path.read_text(encoding="utf-8", errors="replace"))
        return 0

    if shutil.which(glow_bin) is None:
        print(f"glow binary not found: {glow_bin}", file=sys.stderr)
        print(path.read_text(encoding="utf-8", errors="replace"))
        return 0

    proc = subprocess.run([glow_bin, "-p", "-w", str(width), str(path)], check=False)
    return proc.returncode


def collect_stream(
    stream: Iterable[str],
    sink: list[str],
    echo: bool,
    to_stderr: bool,
) -> None:
    target = sys.stderr if to_stderr else sys.stdout
    for chunk in stream:
        sink.append(chunk)
        if echo:
            print(chunk, end="", file=target, flush=True)


def run_goose(prompt: str, goose_bin: str, mode: str) -> tuple[int, str, str, float]:
    if shutil.which(goose_bin) is None:
        raise FileNotFoundError(f"goose binary not found: {goose_bin}")

    command = [goose_bin, "run", "--instructions", "-", "--no-session", "--quiet"]
    start = time.monotonic()
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    out_chunks: list[str] = []
    err_chunks: list[str] = []
    echo_stream = mode == "stream"

    t_out = threading.Thread(target=collect_stream, args=(proc.stdout, out_chunks, echo_stream, False))
    t_err = threading.Thread(target=collect_stream, args=(proc.stderr, err_chunks, echo_stream, True))
    t_out.start()
    t_err.start()

    proc.stdin.write(prompt)
    proc.stdin.close()

    returncode = proc.wait()
    t_out.join()
    t_err.join()
    duration = time.monotonic() - start

    return returncode, "".join(out_chunks), "".join(err_chunks), duration


def validate_answer(answer: str, _goose_exit_code: int) -> ValidationResult:
    reasons: list[str] = []
    required_sections = list("ABCDEFGHIJ")

    section_hits = 0
    for section in required_sections:
        if re.search(rf"(?m)^\s*(?:#+\s*)?{section}\.\s+", answer):
            section_hits += 1
    ratio = section_hits / len(required_sections)

    if ratio < SKELETON_THRESHOLD:
        reasons.append(f"skeleton_ratio_below_threshold_{ratio:.2f}")

    return ValidationResult(
        passed=not reasons,
        ratio=ratio,
        matched_sections=section_hits,
        total_sections=len(required_sections),
        reasons=reasons,
    )


def write_history(
    question: str,
    answer: str,
    stderr: str,
    exit_code: int,
    duration: float,
    attempts_used: int,
    validation: ValidationResult,
) -> Path:
    history_dir = get_history_dir()
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"unable to create history directory '{history_dir}': {exc}") from exc

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{slugify(question)}.md"
    path = history_dir / filename

    body = [
        "# Goose Runtime Map Record",
        "",
        f"- timestamp: {timestamp}",
        f"- question: {question}",
        f"- exit_code: {exit_code}",
        f"- duration_seconds: {duration:.3f}",
        f"- attempts_used: {attempts_used}",
        f"- validation_passed: {validation.passed}",
        f"- skeleton_ratio: {validation.ratio:.3f}",
        f"- matched_sections: {validation.matched_sections}/{validation.total_sections}",
        f"- validation_reasons: {', '.join(validation.reasons) if validation.reasons else '(none)'}",
        "- command: goose run --instructions - --no-session --quiet",
        "",
        "## Question",
        "",
        question,
        "",
        "## Answer",
        "",
        answer.strip() or "(empty answer)",
    ]

    if stderr.strip():
        body.extend(["", "## Stderr", "", "```text", stderr.rstrip(), "```"])

    try:
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"unable to write history file '{path}': {exc}") from exc
    return path


def resolve_question(args: argparse.Namespace) -> str | None:
    question = args.question.strip() if args.question else ""
    if question and args.question_pos:
        raise ValueError("Use --question or positional text, not both")
    if not question and args.question_pos:
        question = " ".join(args.question_pos).strip()
    return question or None


def run_history_mode(args: argparse.Namespace) -> int:
    entries = list_history_entries()
    if not entries:
        print("No history entries found.", file=sys.stderr)
        return 1

    picked = pick_history_entry(entries, args.status)
    if not picked:
        print("No entry selected.", file=sys.stderr)
        return 1

    print_status(f"[status] viewing: {picked.path}", "stage", args.status)
    return render_markdown(picked.path, args.glow_bin, args.width, args.no_glow)


def run_question_mode(question: str, args: argparse.Namespace) -> int:
    print_status("[status] preparing prompt", "stage", args.status)
    prompt = PROMPT_TEMPLATE.format(question=question)

    attempts_used = 0
    exit_code = 1
    answer = ""
    stderr_text = ""
    duration = 0.0
    validation = ValidationResult(
        passed=False,
        ratio=0.0,
        matched_sections=0,
        total_sections=10,
        reasons=["not_run"],
    )

    for attempt in range(1, MAX_RETRIES + 1):
        attempts_used = attempt
        print_status(
            f"[status] running goose (attempt {attempt}/{MAX_RETRIES})",
            "stage",
            args.status,
        )
        exit_code, answer, stderr_text, duration = run_goose(prompt, args.goose_bin, args.status)
        validation = validate_answer(answer, exit_code)

        validation_msg = (
            f"[status] validation attempt {attempt}: "
            f"{validation.matched_sections}/{validation.total_sections} "
            f"({validation.ratio:.0%}) -> {'pass' if validation.passed else 'fail'}"
        )
        print_status(validation_msg, "stage", args.status)

        if validation.passed:
            break
        if attempt < MAX_RETRIES:
            print_status(
                f"[status] retrying due to: {', '.join(validation.reasons)}",
                "stage",
                args.status,
            )

    print_status("[status] writing history", "stage", args.status)
    history_path = write_history(
        question=question,
        answer=answer,
        stderr=stderr_text,
        exit_code=exit_code,
        duration=duration,
        attempts_used=attempts_used,
        validation=validation,
    )

    print_status(f"[status] history saved: {history_path}", "stage", args.status)
    print_status("[status] rendering final answer", "stage", args.status)
    render_code = render_markdown(history_path, args.glow_bin, args.width, args.no_glow)

    if not validation.passed:
        print(
            (
                f"answer validation failed after {attempts_used} attempts: "
                f"{', '.join(validation.reasons)}"
            ),
            file=sys.stderr,
        )
        if exit_code != 0:
            return exit_code
        if render_code != 0:
            return render_code
        return VALIDATION_FAILED_EXIT_CODE

    if exit_code != 0:
        print(f"goose exited with code {exit_code}", file=sys.stderr)
    return exit_code if exit_code != 0 else render_code


def main() -> int:
    args = parse_args()
    try:
        question = resolve_question(args)
        if args.history or question is None:
            return run_history_mode(args)
        return run_question_mode(question, args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
