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

ROOT_DIR = Path(__file__).resolve().parent
HISTORY_DIR = ROOT_DIR / "history"

PROMPT_TEMPLATE = """你現在不是做專案概覽，而是在做一次「問題導向的 runtime map」。

問題：
{question}

要求：
1. 只關注與這個問題直接相關的程式路徑，不要做泛化的 repo 導覽。
2. 先找出入口函數、核心函數、下游副作用點。
3. 說明資料或狀態是如何流動的，而不是只列出檔案。
4. 區分：
   - primary path
   - fallback path
   - repair/reconcile path
5. 對每個關鍵函數，說明：
   - role
   - inputs
   - reads
   - writes
   - side effects
   - downstream calls
6. 若某段是推論而非直接可見事實，必須明確標示為 inference。
7. 最後輸出一份簡化的 runtime map，而不是一般摘要。

輸出格式（中文輸出）：
A. Answer summary
B. Relevant files/functions
C. Execution path
D. State/data flow
E. Side effects
F. Path classification
G. Uncertainties / open questions
"""


@dataclass
class HistoryEntry:
    path: Path
    timestamp: str
    question: str


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


def list_history_entries() -> list[HistoryEntry]:
    if not HISTORY_DIR.exists():
        return []

    entries: list[HistoryEntry] = []
    for path in sorted(HISTORY_DIR.glob("*.md"), reverse=True):
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


def write_history(question: str, answer: str, stderr: str, exit_code: int, duration: float) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{slugify(question)}.md"
    path = HISTORY_DIR / filename

    body = [
        "# Goose Runtime Map Record",
        "",
        f"- timestamp: {timestamp}",
        f"- question: {question}",
        f"- exit_code: {exit_code}",
        f"- duration_seconds: {duration:.3f}",
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

    path.write_text("\n".join(body) + "\n", encoding="utf-8")
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

    print_status("[status] running goose", "stage", args.status)
    exit_code, answer, stderr_text, duration = run_goose(prompt, args.goose_bin, args.status)

    print_status("[status] writing history", "stage", args.status)
    history_path = write_history(
        question=question,
        answer=answer,
        stderr=stderr_text,
        exit_code=exit_code,
        duration=duration,
    )

    print_status(f"[status] history saved: {history_path}", "stage", args.status)
    print_status("[status] rendering final answer", "stage", args.status)
    render_code = render_markdown(history_path, args.glow_bin, args.width, args.no_glow)

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
