"""Offline, checksummed SQLite + checkpoint + audio backup and non-overwriting restore."""
import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .domain import DomainError
from .workspace_lock import workspace_lock

DB_FILES = ('meetflow.db', 'checkpoints.db', 'sales-checkpoints.db')


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def sqlite_check(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise DomainError(f'{path.name}完整性检查未通过', 409)
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise DomainError(f'{path.name}有外键违例', 409)


def backup(data_dir, output):
    data_dir, output = data_dir.resolve(), output.resolve()
    if not (data_dir / 'meetflow.db').is_file():
        raise DomainError('未找到主库；当前备份工具用于默认SQLite资料目录', 422)
    if output.exists() or output == data_dir or output.is_relative_to(data_dir / 'audio'):
        raise DomainError('备份输出须为新的独立目录，不能覆盖已有目录或写入音频目录', 409)
    with workspace_lock(data_dir, exclusive=True):
        output.mkdir(parents=True)
        manifest = {'format': 1, 'status': 'incomplete', 'created_at': datetime.now(UTC).isoformat(), 'files': []}
        (output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        for name in DB_FILES:
            source = data_dir / name
            if source.exists():
                with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(output / name)) as dst:
                    src.backup(dst)
                    dst.execute('PRAGMA journal_mode=DELETE')
                    dst.commit()
                sqlite_check(output / name)
        for folder in ('audio', 'sales-audio'):
            audio = data_dir / folder
            if not audio.exists():
                continue
            for source in sorted(audio.rglob('*')):
                if source.is_file():
                    if not source.resolve().is_relative_to(audio.resolve()):
                        raise DomainError('音频目录包含指向目录外的链接，已停止备份', 409)
                    destination = output / source.relative_to(data_dir)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
        for path in sorted(p for p in output.rglob('*') if p.is_file() and p != output / 'manifest.json'):
            manifest['files'].append({'path': path.relative_to(output).as_posix(), 'size': path.stat().st_size, 'sha256': sha(path)})
        manifest['status'] = 'complete'
        (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'status': 'complete', 'files': len(manifest['files']), 'output': str(output)}


def restore(bundle, destination):
    bundle, destination = bundle.resolve(), destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise DomainError('恢复只允许新的空目录，原资料目录保持原样', 409)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1 or manifest.get('status') != 'complete':
        raise DomainError('备份未完整结束或格式不支持，拒绝恢复', 409)
    validated, seen = [], set()
    for item in manifest['files']:
        path = PurePosixPath(item['path'])
        if path.is_absolute() or '..' in path.parts or '\\' in item['path'] or ':' in item['path']:
            raise DomainError('备份包含越界路径', 409)
        allowed = item['path'] in DB_FILES or path.parts and path.parts[0] in {'audio', 'sales-audio'}
        canonical = item['path'].casefold()
        if not allowed or canonical in seen:
            raise DomainError('备份包含未允许或重复的文件', 409)
        seen.add(canonical)
        source = bundle / item['path']
        if not source.resolve().is_relative_to(bundle) or not source.is_file() or source.stat().st_size != item['size'] or sha(source) != item['sha256']:
            raise DomainError('备份文件缺失或校验不一致，未写入目标目录', 409)
        if item['path'] in DB_FILES:
            sqlite_check(source)
        validated.append((source, item['path']))
    if 'meetflow.db' not in seen:
        raise DomainError('备份缺少主库', 409)
    with workspace_lock(destination, exclusive=True):
        for source, relative in validated:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return {'status': 'restored', 'files': len(validated), 'destination': str(destination)}


def main():
    parser = argparse.ArgumentParser(description='MeetFlow离线备份与新目录恢复；请先停止服务')
    sub = parser.add_subparsers(dest='operation', required=True)
    command = sub.add_parser('backup')
    command.add_argument('--data-dir', type=Path, default=Path('data'))
    command.add_argument('--output', type=Path, required=True)
    command = sub.add_parser('restore')
    command.add_argument('--bundle', type=Path, required=True)
    command.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = backup(args.data_dir, args.output) if args.operation == 'backup' else restore(args.bundle, args.destination)
    except DomainError as exc:
        parser.exit(1, exc.message + '\n')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
