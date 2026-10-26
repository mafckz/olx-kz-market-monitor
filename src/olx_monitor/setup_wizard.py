from __future__ import annotations

import getpass
import secrets
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import keyring
from keyring.errors import KeyringError

from olx_monitor.ai import KEYRING_SERVICE, OPENAI_KEY_NAME
from olx_monitor.bot import TELEGRAM_KEY_NAME
from olx_monitor.database import Database
from olx_monitor.paths import ensure_directories
from olx_monitor.settings import Settings


def _check_keyring() -> None:
    try:
        backend = keyring.get_keyring()
        if getattr(backend, "priority", 0) <= 0:
            raise RuntimeError("Не найдено системное хранилище секретов.")
        probe_account = "setup_probe"
        probe_value = secrets.token_urlsafe(18)
        keyring.set_password(KEYRING_SERVICE, probe_account, probe_value)
        actual = keyring.get_password(KEYRING_SERVICE, probe_account)
        keyring.delete_password(KEYRING_SERVICE, probe_account)
        if actual != probe_value:
            raise RuntimeError("Windows Credential Manager не подтвердил сохранение секрета.")
    except (KeyringError, RuntimeError) as exc:
        raise RuntimeError(
            "Безопасное системное хранилище недоступно. Настройка остановлена; токены не записаны в файл."
        ) from exc


def _input_secret(label: str, existing: bool) -> str | None:
    if sys.platform == "win32":
        import tkinter as tk
        from tkinter import simpledialog

        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            hint = "Ввод скрыт; токен можно вставить из буфера обмена."
            if existing:
                hint += " Нажмите Cancel, чтобы оставить сохранённый токен."
            result = simpledialog.askstring(
                "OLX Market Monitor — безопасный ввод",
                f"{label}\n\n{hint}",
                show="*",
                parent=root,
            )
        except tk.TclError as exc:
            raise RuntimeError(
                "Не удалось открыть окно безопасного ввода. Повторите настройку в обычной интерактивной сессии Windows."
            ) from exc
        finally:
            if root is not None:
                root.destroy()
        value = result.strip() if result is not None else ""
    else:
        prompt = f"{label}{' (Enter — оставить сохранённый)' if existing else ''}: "
        value = getpass.getpass(prompt).strip()
    if not value and existing:
        return None
    if not value:
        raise ValueError(f"Поле «{label}» не может быть пустым.")
    return value


def _verify_telegram(token: str) -> dict[str, Any]:
    try:
        response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Не удалось связаться с Telegram Bot API: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError("Telegram не принял токен. Проверьте токен от @BotFather.")
    payload = response.json()
    if not payload.get("ok") or not payload.get("result", {}).get("is_bot"):
        raise RuntimeError("Telegram вернул некорректный результат проверки бота.")
    return payload["result"]


def _verify_openai(key: str, model: str) -> None:
    try:
        response = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=25,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Не удалось проверить OpenAI API key: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI не принял API key (HTTP {response.status_code}).")
    available_models = {item.get("id") for item in response.json().get("data", [])}
    if available_models and model not in available_models:
        raise RuntimeError(f"Модель {model} не видна этому API key. Проверьте ID модели и доступ в OpenAI Platform.")


def configure(home: Path | None = None) -> None:
    root = ensure_directories(home)
    database = Database(root / "monitor.sqlite3")
    database.seed_initial_profile()
    settings = Settings.load(root)
    _check_keyring()
    print("Локальная настройка OLX Market Monitor")
    print(f"Локальные данные: {root}")

    existing_telegram = bool(keyring.get_password(KEYRING_SERVICE, TELEGRAM_KEY_NAME))
    telegram_token = _input_secret("Токен Telegram-бота от @BotFather", existing_telegram)
    if telegram_token:
        identity = _verify_telegram(telegram_token)
        keyring.set_password(KEYRING_SERVICE, TELEGRAM_KEY_NAME, telegram_token)
        print(f"Telegram-бот проверен: @{identity.get('username', 'без имени')}")
    elif not existing_telegram:
        raise RuntimeError("Telegram Bot Token не настроен.")

    existing_openai = bool(keyring.get_password(KEYRING_SERVICE, OPENAI_KEY_NAME))
    enabled = input("Настроить необязательный OpenAI AI-режим? [y/N]: ").strip().casefold()
    if enabled in {"y", "yes", "д", "да"}:
        openai_key = _input_secret("OpenAI API key", existing_openai)
        candidate_key = openai_key or keyring.get_password(KEYRING_SERVICE, OPENAI_KEY_NAME)
        model = input(f"Модель Responses API [{settings.ai_model}]: ").strip() or settings.ai_model
        if not candidate_key:
            raise ValueError("Для AI-режима нужен OpenAI API key.")
        _verify_openai(candidate_key, model)
        if openai_key:
            keyring.set_password(KEYRING_SERVICE, OPENAI_KEY_NAME, openai_key)
        print("OpenAI API key принят; проверка доступа к моделям не вызывает генерацию.")
        budget_raw = input(
            f"Локальный месячный предел AI в USD [{max(settings.ai_monthly_budget_usd, 5.0):.2f}]: "
        ).strip()
        budget = float(budget_raw) if budget_raw else max(settings.ai_monthly_budget_usd, 5.0)
        if budget <= 0:
            raise ValueError("Лимит AI-расходов должен быть больше нуля.")
        same_model = model == settings.ai_model
        input_default = settings.ai_input_usd_per_million if same_model else 0.0
        output_default = settings.ai_output_usd_per_million if same_model else 0.0
        input_hint = f"{input_default:g}" if input_default else "укажите"
        output_hint = f"{output_default:g}" if output_default else "укажите"
        input_rate_raw = input(f"Цена модели за 1M input tokens USD [{input_hint}]: ").strip()
        output_rate_raw = input(f"Цена модели за 1M output tokens USD [{output_hint}]: ").strip()
        input_rate = float(input_rate_raw) if input_rate_raw else input_default
        output_rate = float(output_rate_raw) if output_rate_raw else output_default
        if input_rate <= 0 or output_rate <= 0:
            raise ValueError("Цены токенов должны быть больше нуля.")
        settings = replace(
            settings,
            ai_model=model,
            ai_monthly_budget_usd=budget,
            ai_input_usd_per_million=input_rate,
            ai_output_usd_per_million=output_rate,
            ai_prices_checked_on=datetime.now(UTC).date().isoformat(),
        )
    elif existing_openai:
        keep = input("Оставить сохранённый OpenAI API key? [Y/n]: ").strip().casefold()
        if keep in {"n", "no", "н", "нет"}:
            keyring.delete_password(KEYRING_SERVICE, OPENAI_KEY_NAME)
            settings = replace(settings, ai_monthly_budget_usd=0.0)

    settings.save()
    owner = database.get_setting("owner_chat_id")
    if owner:
        print("Telegram уже привязан к владельцу; код повторной привязки не создавался.")
    else:
        code = secrets.token_hex(3).upper()
        database.save_setting("pairing_code", code)
        print("\nОдноразовый код Telegram-привязки (не публикуйте его):")
        print(code)
        print("Откройте своего бота в Telegram и отправьте: /pair " + code)
    print("\nПосле привязки запустите: python -m olx_monitor run")
    print("Для автозапуска Windows выполните setup.ps1 с параметром -InstallTask.")
