from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class CodexRunnerError(RuntimeError):
    pass


@dataclass
class CodexRunResult:
    stdout: str
    stderr: str
    returncode: int


def run_codex_exec(
    *,
    root: Path,
    prompt: str,
    settings: Mapping[str, Any],
    last_message_path: Path,
) -> CodexRunResult:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise CodexRunnerError("codex CLI was not found on PATH.")

    codex_settings = settings.get("codex", {})
    command = [
        codex_bin,
        "exec",
        "-C",
        str(root),
        "-s",
        str(codex_settings.get("sandbox", "workspace-write")),
        "-o",
        str(last_message_path),
        "-",
    ]

    model = str(codex_settings.get("model", "")).strip()
    if model:
        command[2:2] = ["-m", model]

    for extra_dir in codex_settings.get("extra_writable_dirs", []):
        if extra_dir:
            command.extend(["--add-dir", str(Path(extra_dir).expanduser())])

    proc = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    return CodexRunResult(
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
    )
