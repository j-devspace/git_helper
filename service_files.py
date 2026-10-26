# Collect uploadable local files with ignore rules, sensitive-path exclusions and size limits.
# 제외 규칙, 민감 경로 제외, 크기 제한을 적용해 업로드할 로컬 파일을 수집합니다.

from __future__ import annotations

import base64
from pathlib import Path

from pathspec import GitIgnoreSpec


def read_folder(folder_path: str) -> tuple[list[dict[str, str]], list[str]]:
    root = Path(folder_path).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("folder_path must be an existing absolute directory.")
    root = root.resolve()
    files: list[dict[str, str]] = []
    excluded: list[str] = []
    total_bytes = 0
    blocked_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ssh"}

    def visit(directory: Path, specs: list[tuple[Path, GitIgnoreSpec]]) -> None:
        nonlocal total_bytes
        ignore = directory / ".gitignore"
        if ignore.is_file() and not ignore.is_symlink():
            specs = [*specs, (directory, GitIgnoreSpec.from_lines(
                ignore.read_text(encoding="utf-8-sig").splitlines()
            ))]
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root).as_posix()
            name = path.name.lower()
            if (
                path.is_symlink()
                or (hasattr(path, "is_junction") and path.is_junction())
                or not path.resolve().is_relative_to(root)
                or name in blocked_dirs
                or name == ".env"
                or name.startswith(".env.")
                or name in {"id_rsa", "id_ed25519", ".npmrc", ".pypirc"}
                or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
            ):
                excluded.append(relative)
                continue
            is_directory = path.is_dir()
            ignored = False
            for base, spec in specs:
                candidate = path.relative_to(base).as_posix()
                match = spec.check_file(candidate + ("/" if is_directory else ""))
                if match.include is not None:
                    ignored = match.include
            if ignored:
                excluded.append(relative)
                continue
            if is_directory:
                visit(path, specs)
            elif path.is_file():
                size = path.stat().st_size
                if len(files) >= 1000 or total_bytes + size > 20 * 1024 * 1024:
                    raise ValueError("Folder exceeds 1000 files or 20 MiB upload limit.")
                data = path.read_bytes()
                total_bytes += len(data)
                if total_bytes > 20 * 1024 * 1024:
                    raise ValueError("Folder exceeds 20 MiB upload limit.")
                files.append({
                    "file_path": relative,
                    "content": base64.b64encode(data).decode("ascii"),
                    "encoding": "base64",
                })
            else:
                raise ValueError(f"Unsupported filesystem entry: {relative}")

    visit(root, [])
    if not files:
        raise ValueError("No uploadable files remain after exclusions.")
    return files, excluded
