#!/usr/bin/env python3
"""goose-runtime-map (grm) — drop-in terminal shim to log Q&A into a goose session.

Usage:
    grm "your question"           # interactive Q&A, writes on explicit "DONE"
    grm -a "your question"       # auto-mode: question + answer as a single turn
    alias grm='python3 /path/to/grm.py'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

VERSION = "0.1.0"


def _get_goose_dir() -> Path:
    """Return the goose directory path from env or default."""
    goose_dir = os.environ.get("GOOSE_DIR")
    if goose_dir:
        return Path(goose_dir)
    # Default: ~/.goose
    home = Path.home()
    return home / ".goose"


def _get_session_id() -> str:
    """Return current session ID from env or generate a default."""
    return os.environ.get("GOOSE_SESSION_ID", "default")


def _get_sessions_dir(session_id: str) -> Path:
    """Return the sessions directory for the given session."""
    goose_dir = _get_goose_dir()
    sessions_dir = goose_dir / "sessions" / session_id
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def _get_history_path(session_id: str) -> Path:
    """Return the history file path for the given session."""
    return _get_sessions_dir(session_id) / "history.jsonl"


def _append_to_history(session_id: str, entry: dict) -> None:
    """Append a single entry to the history file."""
    history_path = _get_history_path(session_id)
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_history(session_id: str) -> list[dict]:
    """Read the entire history for a session."""
    history_path = _get_history_path(session_id)
    if not history_path.exists():
        return []
    entries = []
    with open(history_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _write_history_file(session_id: str, entries: list[dict]) -> None:
    """Write the entire history to the history file (overwrite mode)."""
    history_path = _get_history_path(session_id)
    with open(history_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _get_current_context(session_id: str) -> str:
    """Read and concatenate all user messages from history as context."""
    history = _read_history(session_id)
    context_parts = []
    for entry in history:
        if entry.get("type") == "user" and entry.get("content"):
            context_parts.append(entry["content"])
    return "\n\n".join(context_parts)


def _run_goose_command(question: str, context: str = "") -> str:
    """Run goose command and return the AI's response."""
    full_prompt = question
    if context:
        full_prompt = f"Context from previous turns:\n{context}\n\nCurrent question:\n{question}"

    cmd = ["goose", "-q", full_prompt]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return f"Error running goose: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "Error: goose command timed out after 120s"
    except FileNotFoundError:
        return "Error: goose command not found. Please ensure goose is installed."


def _is_valid_answer(answer: str) -> bool:
    """Check if an answer is valid (non-empty and not an error message)."""
    if not answer or not answer.strip():
        return False
    if answer.startswith("Error:"):
        return False
    return True


def validate_answer(answer: str, question: str, strict: bool = False) -> tuple[bool, str]:
    """Validate an answer.
    
    Returns:
        (is_valid, message)
    """
    if not answer or not answer.strip():
        return False, "Answer is empty"
    
    if answer.startswith("Error:"):
        return False, "Answer contains an error"
    
    if strict and len(answer) < 10:
        return False, f"Answer too short ({len(answer)} chars)"
    
    # Check if answer seems to relate to the question
    if strict and len(question) > 0:
        # Simple heuristic: answer should be at least somewhat substantial
        if len(answer) < len(question) * 0.1:  # Answer at least 10% of question length
            return False, "Answer seems too short relative to question"
    
    return True, "Answer is valid"


def write_history(session_id: str, question: str, answer: str, validate: bool = True) -> bool:
    """Write a Q&A pair to history, with optional validation.
    
    Returns:
        True if successfully written, False otherwise
    """
    # Validate answer if requested
    if validate:
        is_valid, msg = validate_answer(answer, question, strict=True)
        if not is_valid:
            print(f"Validation failed: {msg}", file=sys.stderr)
            return False
    
    # Create history entries
    user_entry = {
        "type": "user",
        "content": question,
        "timestamp": "2024-01-01T00:00:00Z"  # Simplified for now
    }
    assistant_entry = {
        "type": "assistant",
        "content": answer,
        "timestamp": "2024-01-01T00:00:01Z"  # Simplified for now
    }
    
    # Append to history
    _append_to_history(session_id, user_entry)
    _append_to_history(session_id, assistant_entry)
    
    return True


def run_question_mode(question: str, auto_answer: bool = False) -> None:
    """Run interactive question mode.
    
    Args:
        question: The question to ask
        auto_answer: If True, automatically get answer from goose and write
    """
    session_id = _get_session_id()
    
    # Get existing context
    context = _get_current_context(session_id)
    
    if auto_answer:
        # Auto mode: run goose, get answer, validate, write
        print(f"Question: {question}")
        print("Getting answer from goose...")
        
        # Retry loop with validation
        max_retries = 3
        for attempt in range(max_retries):
            answer = _run_goose_command(question, context)
            
            is_valid, msg = validate_answer(answer, question, strict=True)
            if is_valid:
                print(f"Answer: {answer}")
                
                # Write to history with validation
                success = write_history(session_id, question, answer, validate=True)
                if success:
                    print("\nQ&A written to history.")
                    return
                else:
                    print(f"\nAttempt {attempt + 1}: Failed to write history")
                    if attempt + 1 < max_retries:
                        print("Retrying...")
                        continue
                    else:
                        print("Max retries reached. Exiting.")
                        return
            else:
                print(f"\nAttempt {attempt + 1}: {msg}")
                if attempt + 1 < max_retries:
                    print("Retrying with fresh request...")
                    continue
                else:
                    print("Max retries reached. Exiting.")
                    return
        
        print("Failed after all retries.", file=sys.stderr)
        sys.exit(1)
    
    else:
        # Interactive mode: show question, wait for user to type answer
        print(f"\nQuestion: {question}")
        print("Type your answer below (or 'DONE' to finish):")
        print("-" * 40)
        
        answer_lines = []
        while True:
            try:
                line = input()
                if line.strip().upper() == "DONE":
                    break
                answer_lines.append(line)
            except EOFError:
                break
        
        answer = "\n".join(answer_lines).strip()
        
        if not answer:
            print("No answer provided. Nothing written.", file=sys.stderr)
            return
        
        # Validate before writing
        is_valid, msg = validate_answer(answer, question, strict=False)
        if not is_valid:
            print(f"Warning: {msg}", file=sys.stderr)
            confirm = input("Write anyway? (y/N): ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return
        
        # Write to history (with lenient validation in interactive mode)
        success = write_history(session_id, question, answer, validate=False)
        if success:
            print("Answer written to history.")
        else:
            print("Failed to write history.", file=sys.stderr)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="goose-runtime-map: Terminal shim to log Q&A into goose session"
    )
    parser.add_argument(
        "question",
        nargs="?",
        help="The question to ask"
    )
    parser.add_argument(
        "-a", "--auto",
        action="store_true",
        help="Auto mode: question + answer as single turn"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}"
    )
    
    args = parser.parse_args()
    
    if not args.question:
        parser.print_help()
        sys.exit(1)
    
    run_question_mode(args.question, auto_answer=args.auto)


if __name__ == "__main__":
    main()
