from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from olx_monitor.database import Database
from olx_monitor.paths import ensure_directories
from olx_monitor.reporting import build_digest, write_markdown_report
from olx_monitor.settings import Settings


def _configure_logging(home: Path) -> None:
    log_path = home / "logs" / "monitor.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="olx-monitor", description="Локальный монитор объявлений OLX.kz")
    sub = parser.add_subparsers(dest="command")
    configure = sub.add_parser("configure", help="Интерактивная локальная настройка и секреты")
    configure.add_argument("--home", type=Path)
    run = sub.add_parser("run", help="Запустить Telegram-бота и расписание")
    run.add_argument("--home", type=Path)
    once = sub.add_parser("once", help="Выполнить один цикл без Telegram polling")
    once.add_argument("--home", type=Path)
    return parser


async def _run_once(home: Path) -> int:
    from olx_monitor.monitor import Monitor

    settings = Settings.load(home)
    database = Database(settings.home / "monitor.sqlite3")
    database.seed_initial_profile()
    stats = await Monitor(settings, database).run()
    digest = build_digest(stats, {profile.id: profile for profile in database.profiles()})
    print(digest or "Проверка завершена. Новых объявлений и существенных изменений нет.")
    print(f"Отчёт: {write_markdown_report(database, settings.home)}")
    return 1 if stats.error else 0


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    command = args.command or "run"
    requested_home = getattr(args, "home", None)
    if command == "configure":
        from olx_monitor.setup_wizard import configure

        configure(requested_home)
        return
    settings = Settings.load(requested_home)
    ensure_directories(settings.home)
    _configure_logging(settings.home)
    database = Database(settings.home / "monitor.sqlite3")
    database.seed_initial_profile()
    if command == "once":
        raise SystemExit(asyncio.run(_run_once(settings.home)))
    if command == "run":
        from olx_monitor.bot import run_bot

        run_bot(settings, database)
        return
    parser.error(f"Неизвестная команда: {command}")
