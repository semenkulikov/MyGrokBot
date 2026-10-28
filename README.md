# MyGrokBot — Telegram-мост к Джарвису (Grok Bot)

Личный Telegram-бот, который работает локально на Маке и связывает тебя с Grok Bot:
пишешь задачу боту в Telegram → бот передаёт её Grok Bot через вебхук → Grok Bot
выполняет задачу (в том числе на этом Маке) и кладёт ответ в папку → бот присылает ответ в чат.

## Как это работает

```
Telegram ──► бот (aiogram, long polling)
               │  1. сохраняет задачу (task_id → chat_id) в runtime/tasks.sqlite3
               │  2. отвечает «Принял, работаю…»
               │  3. POST WEBHOOK_URL  {"task_id", "chat_id", "text", "outbox_dir"}
               ▼
           Grok Bot (регулярная задача, запускается вебхуком)
               │  выполняет задачу и пишет ответ:
               │    runtime/outbox/<task_id>.png  (необязательно, кладётся первым)
               │    runtime/outbox/<task_id>.txt  (текст ответа — сигнал «готово»)
               ▼
бот каждые OUTBOX_POLL_SECONDS проверяет runtime/outbox,
отправляет текст (и картинку) в нужный чат и переносит файлы в runtime/sent/
```

- Писать задачи могут только ID из `ALLOWED_USER_IDS`; остальные игнорируются и пишутся в лог.
- Если ответ лежит в outbox, а задачи с таким `task_id` нет в базе, он уходит первому ID из
  `ALLOWED_USER_IDS` — так Grok Bot может сам написать тебе (например, уведомление).
- Если ответ не удаётся отправить 5 раз подряд, файлы переносятся в `runtime/failed/`.
- Если вебхук недоступен, бот делает несколько попыток с нарастающей паузой и сообщает об ошибке в чат.

### Команды

| Команда   | Что делает                                                        |
|-----------|-------------------------------------------------------------------|
| `/start`  | Приветствие и подсказка                                           |
| `/status` | Жив ли бот, аптайм, сколько задач в работе, настроен ли вебхук    |
| `/id`     | Присылает твой Telegram ID (работает для всех — чтобы узнать ID)  |

## Установка

Нужен Python 3.12 и [uv](https://docs.astral.sh/uv/).

```bash
cd ~/CursorProjects/MyGrokBot
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
# для разработки и тестов:
uv pip install --python .venv/bin/python -r requirements-dev.txt
cp .env.example .env   # и заполнить значения
```

## Настройка `.env`

| Переменная               | Описание                                                          |
|--------------------------|-------------------------------------------------------------------|
| `BOT_TOKEN`              | Токен бота от @BotFather (обязательно)                            |
| `ALLOWED_USER_IDS`       | Разрешённые Telegram ID через запятую                             |
| `WEBHOOK_URL`            | URL вебхука регулярной задачи Grok Bot                            |
| `WEBHOOK_KEY`            | Ключ вебхука                                                      |
| `WEBHOOK_AUTH_HEADER`    | Заголовок для ключа, по умолчанию `Authorization`                 |
| `WEBHOOK_AUTH_SCHEME`    | Схема, по умолчанию `Bearer`; `none` — передавать ключ как есть   |
| `WEBHOOK_TIMEOUT_SECONDS`| Таймаут запроса, по умолчанию 15                                  |
| `WEBHOOK_MAX_ATTEMPTS`   | Количество попыток, по умолчанию 3                                |
| `OUTBOX_POLL_SECONDS`    | Как часто проверять outbox, по умолчанию 2                        |
| `LOG_LEVEL`              | Уровень логов, по умолчанию `INFO`                                |

Свой Telegram ID проще всего узнать так: запусти бота с пустым `ALLOWED_USER_IDS`,
отправь ему `/id`, впиши полученное число в `.env` и перезапусти.

## Запуск

```bash
.venv/bin/python -m grok_bridge
# или
./scripts/run.sh
```

Логи пишутся в консоль и в `logs/bot.log` (с ротацией).

### Автозапуск (launchd)

Пример лежит в `deploy/com.semenkulikov.mygrokbot.plist`, инструкция по установке — внутри файла.

## Тесты

```bash
.venv/bin/python -m pytest
```

## Структура

```
grok_bridge/
├── __main__.py              # точка входа, сборка зависимостей
├── config.py                # настройки (pydantic-settings, .env)
├── logging_setup.py         # консоль + logs/bot.log
├── storage.py               # SQLite: task_id → chat_id, статусы
├── bot/
│   ├── handlers.py          # /start, /status, /id, приём задач
│   └── middlewares.py       # доступ только для ALLOWED_USER_IDS
└── services/
    ├── webhook_client.py    # отправка задач на вебхук (aiohttp, ретраи)
    └── outbox_watcher.py    # доставка ответов из runtime/outbox
```
