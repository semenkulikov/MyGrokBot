import os
import time
from pathlib import Path

from grok_bridge.services.janitor import Janitor, cleanup_dir, cleanup_rotated_logs

DAY = 86400


def _set_age(path: Path, days: float) -> None:
    ts = time.time() - days * DAY
    os.utime(path, (ts, ts))


def test_cleanup_dir_removes_old_files_and_folders(tmp_path: Path) -> None:
    old_file = tmp_path / "old.txt"
    old_file.write_text("x")
    _set_age(old_file, 30)
    fresh_file = tmp_path / "fresh.txt"
    fresh_file.write_text("x")

    old_dir = tmp_path / "task-old"
    old_dir.mkdir()
    (old_dir / "a.ogg").write_text("x")
    _set_age(old_dir / "a.ogg", 30)
    _set_age(old_dir, 30)

    mixed_dir = tmp_path / "task-mixed"  # старая папка, но внутри свежий файл
    mixed_dir.mkdir()
    (mixed_dir / "new.jpg").write_text("x")
    _set_age(mixed_dir, 30)

    assert cleanup_dir(tmp_path, 14 * DAY) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fresh.txt", "task-mixed"]
    assert cleanup_dir(tmp_path / "missing", DAY) == 0


def test_cleanup_rotated_logs_keeps_current(tmp_path: Path) -> None:
    for name in ("bot.log", "bot.log.1", "bot.log.2", "launchd.err.log"):
        (tmp_path / name).write_text("x")
        _set_age(tmp_path / name, 30)
    (tmp_path / "bot.log.3").write_text("x")

    assert cleanup_rotated_logs(tmp_path, 14 * DAY) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bot.log", "bot.log.3", "launchd.err.log"]


def test_janitor_run_once(tmp_path: Path) -> None:
    sent, logs = tmp_path / "sent", tmp_path / "logs"
    sent.mkdir()
    logs.mkdir()
    (sent / "t.txt").write_text("x")
    _set_age(sent / "t.txt", 3)

    assert Janitor([sent], logs, max_age_days=7).run_once() == 0
    assert Janitor([sent], logs, max_age_days=1).run_once() == 1
