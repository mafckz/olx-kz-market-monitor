# Передача проекта OLX KZ Market Monitor

Обновлено: 26 октября 2026 г.

Основной приватный репозиторий: [grromankz-ctrl/olx-kz-market-monitor](https://github.com/grromankz-ctrl/olx-kz-market-monitor). Публичный снимок для другой машины: [mafckz/olx-kz-market-monitor](https://github.com/mafckz/olx-kz-market-monitor), доступ анонимно проверен 26.10.2026. Актуальный статус: [docs/project-status.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/project-status.md).

Скопируй раздел «Промпт следующему агенту» целиком в новую сессию и выбери поддерживаемую модель `gpt-6-luna`. После перезапуска Codex начни новый ход в этой сессии.

---

## Промпт следующему агенту

Продолжи разработку **OLX KZ Market Monitor**. Клонируй публичное зеркало:

```bash
git clone https://github.com/mafckz/olx-kz-market-monitor.git
cd olx-kz-market-monitor
git switch main
git pull --ff-only origin main
```

Прежняя проектная активность завершилась штатно, затем Codex перезапустился. При попытках продолжить старую сессию API отклонил `commandcode/deepseek-deepseek-v4.1-flash` как неподдерживаемую модель, а последующие запросы завершились ошибкой `Invalid 'input[112].content'`. Состояние проекта сохранено в репозитории. Работай в новой сессии на `gpt-6-luna`; не выбирай неподдерживаемый model ID `commandcode/deepseek-deepseek-v4.1-flash` в Codex.

Проверь рабочее дерево до изменений. Пароли, одноразовые коды, cookies и API-токены не передавай через чат. Если владелец уже добавил тебя в приватный репозиторий, сверь публичный снимок с [основным репозиторием](https://github.com/grromankz-ctrl/olx-kz-market-monitor); иначе продолжай в своём форке или ветке и создай pull request.

### Сначала прочитай

- [README.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/README.md)
- [AGENTS.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/AGENTS.md)
- [codex-orcestration.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/codex-orcestration.md)
- [docs/project-status.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/project-status.md)
- [docs/task-workflow.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/task-workflow.md)
- [docs/tasks.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/tasks.md)
- [docs/requirements.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/requirements.md)
- [docs/implementation-plan.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/implementation-plan.md)
- [GLOSSARY.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/GLOSSARY.md)
- [ADR 0001](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/adr/0001-configurable-search-profiles.md)
- [ADR 0002 об ограничениях OLX](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/adr/0002-respect-olx-robots-txt.md)

### Устройство проекта

Приложение рассчитано на Windows и Python 3.12+. `olx-monitor configure` запускает [setup_wizard.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/setup_wizard.py), `olx-monitor once` выполняет один цикл без Telegram polling, `olx-monitor run` запускает Telegram-бота. Точки входа находятся в [cli.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/cli.py) и [__main__.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/__main__.py).

Поток: профили из [database.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/database.py) → запросы через [sources/olx.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/sources/olx.py) → обработка в [monitor.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/monitor.py) → [profiles.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/profiles.py) и [facts.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/facts.py) → SQLite и события → отчёт через [reporting.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/reporting.py). [settings.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/settings.py) и [paths.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/paths.py) задают конфигурацию и локальные пути. Telegram находится в [bot.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/bot.py), опциональный AI — в [ai.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/ai.py), установка Windows — в [scripts/setup.ps1](https://github.com/mafckz/olx-kz-market-monitor/blob/main/scripts/setup.ps1).

### Подтверждённое состояние

- **OLX-001 — In progress.** Нужен разовый поиск подержанных настольных ПК в Алматы с точной RTX 3080 за 200 000–500 000 ₸ включительно, без Ti, ноутбуков и отдельных видеокарт. 25.10.2026 `once` завершился кодом 1 после HTTP 403 на `robots.txt`; категория не запрашивалась, live-результатов нет.
- **OLX-003 — In progress.** Приватный PR [#1](https://github.com/grromankz-ctrl/olx-kz-market-monitor/pull/1) и его публичный черновик [#1](https://github.com/mafckz/olx-kz-market-monitor/pull/1) прекращают повторы `robots.txt` при 3xx/4xx, сохраняя ограниченные повторы для временных 5xx/timeout/network errors. Изменение прошло внутреннее независимое ревью. Проверка GitHub 26.10.2026: приватный PR открыт и `mergeable=false`; публичный draft открыт и `mergeable=true`. Ни один не объединён и не встроен в `main`; перепроверь перед действиями.
- **OLX-004 — In progress.** Отдельный поиск видеокарты RTX 3080 в Алматы. 26.10.2026 встроенный браузер получил `net::ERR_BLOCKED_BY_CLIENT`, прямой одноразовый терминальный запрос — HTTP 403 на `robots.txt`; категория не запрашивалась. Web-индекс дал только частичную подборку: рабочие 10/12-гигабайтные RTX 3080 примерно за 150 000–230 000 ₸, неисправная около 70 000 ₸, готовые ПК за 340 000–650 000 ₸. Не считать это полным списком или живой OLX-выдачей.
- **OLX-002 — Planned.** Повторяющийся сбор и локальная история в коде есть; реальный периодический запуск не подтверждён.
- **BOT-001 — Paused.** Telegram отложен пользователем; не повторяй pairing и привязку без нового прямого запроса.
- Автономный поиск по запросам, расписание и доставка результатов пока **не подтверждены**.

### Правила продолжения

Читай [codex-orcestration.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/codex-orcestration.md) и [docs/task-workflow.md](https://github.com/mafckz/olx-kz-market-monitor/blob/main/docs/task-workflow.md). До реализации зарегистрируй содержательную задачу в реестре, выбери Standard или Critical и обеспечь независимое ревью. Тесты запускай только при явном запросе пользователя или когда проверка прямо требуется задачей. Не ставь Done без проверки критериев и принятого ревью.

Соблюдай [robots.txt OLX.kz](https://www.olx.kz/robots.txt) и ADR 0002. При 403, CAPTCHA, 429, блокировке или отказе прекращай запросы к ресурсу; не используй прокси, ротацию IP, обходные API или логин для преодоления защиты. Стандартный OLX API предназначен для управления своими объявлениями, а не для чтения чужих. Профили продавцов и списки их объявлений автоматически не читать; допустима только ссылка из разрешённой карточки для ручной проверки. Ошибку загрузки нельзя превращать в пустую выдачу.

### Ближайший путь

1. Сначала сверь актуальные ветки и PR #1. Приватный PR конфликтует с `main`, публичный draft сейчас mergeable. Не сливай PR без решения владельца и принятого ревью.
2. Для OLX-001/004 запроси у владельца разрешённую выгрузку или материалы объявлений, которые он сам открыл. Не проси пароль и одноразовые коды в чате.
3. После получения разрешённых данных выполни поиск по критериям, сохрани ссылки и цены, разделяй готовые ПК, рабочие карты, неисправные карты и ноутбуки. Не называй подборку полной без проверки всей доступной выдачи.
4. Не повторяй запрос к OLX после HTTP 403, не обходи `robots.txt`, блокировки или запреты доступа. OLX-002 остаётся неподтверждённой; Telegram оставь на паузе.
5. Обнови `docs/tasks.md` и `docs/project-status.md`; пройди требуемое независимое ревью и только тогда обнови статусы.

### Основные файлы

- [pyproject.toml](https://github.com/mafckz/olx-kz-market-monitor/blob/main/pyproject.toml)
- [src/olx_monitor/sources/olx.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/sources/olx.py)
- [src/olx_monitor/monitor.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/monitor.py)
- [src/olx_monitor/profiles.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/profiles.py)
- [src/olx_monitor/database.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/database.py)
- [src/olx_monitor/models.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/models.py)
- [src/olx_monitor/facts.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/facts.py)
- [src/olx_monitor/reporting.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/reporting.py)
- [src/olx_monitor/settings.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/settings.py)
- [src/olx_monitor/paths.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/paths.py)
- [src/olx_monitor/bot.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/bot.py)
- [src/olx_monitor/ai.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/ai.py)
- [src/olx_monitor/cli.py](https://github.com/mafckz/olx-kz-market-monitor/blob/main/src/olx_monitor/cli.py)
- [tests/test_robots_retry_policy.py в ветке исправления](https://github.com/mafckz/olx-kz-market-monitor/blob/fix/olx-003-robots-denial-no-retry/tests/test_robots_retry_policy.py)
