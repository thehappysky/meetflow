"""Resolve logical audio names in the current workspace without rewriting frozen snapshots."""
from pathlib import Path, PureWindowsPath

from .domain import DomainError


def audio_file(data_dir, stored, folder='audio'):
    if not stored or folder not in {'audio', 'sales-audio'}:
        raise DomainError('录音不可用', 404)
    root = (Path(data_dir).resolve() / folder).resolve()
    name = PureWindowsPath(str(stored)).name
    target = (root / name).resolve()
    if not name or name in {'.', '..'} or not target.is_relative_to(root) or not target.is_file():
        raise DomainError('录音不可用', 404)
    return target
