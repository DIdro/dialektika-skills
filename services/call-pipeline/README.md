# call-pipeline

Сервер для рабочих звонков: принимает запись, расшифровывает её, размечает говорящих и готовит
черновик разбора. Запись можно загрузить из CLI или веб-кабинета либо отправить бота в Яндекс
Телемост.

Это снимок кода нашего рабочего сервиса без истории коммитов. Разбор кейса — в
[CASE.md](../../plugins/call-debrief/CASE.md).

## Что внутри

| Папка | Что это |
|---|---|
| `server/app/routes`, `db` | API на FastAPI, очередь задач в SQLite |
| `server/app/workers` | Воркеры: транскрибация, диаризация, классификатор, запуск записи |
| `server/app/telemost` | Запись Телемоста и управляющий Telegram-бот |
| `cli/` | Команда `pipeline`: загрузка, наблюдение за папками, запись, очередь, админка |
| `web/` | Веб-кабинет на React: запись по ссылке, загрузка, библиотека расшифровок, пользователи |
| `deploy/` | Юниты systemd, конфиги nginx, образец переменных окружения |

## Модели

- Транскрибация: [faster-whisper](https://github.com/SYSTRAN/faster-whisper), модель large-v3-turbo.
  Это открытая реализация [Whisper](https://github.com/openai/whisper), работает у вас на сервере.
- Разметка говорящих: [pyannote.audio](https://github.com/pyannote/pyannote-audio), нужен токен
  Hugging Face.
- Классификатор: Claude Sonnet, нужен ключ Anthropic. Необязательные дополнения: Groq для быстрой
  расшифровки сразу после записи, OpenAI для названий записей.

## Как связано с очередью приёмки

Страница «Разбор» в кабинете работает вместе с [ops-ingest](../ops-ingest): он забирает расшифровки,
разбирает их и держит очередь. Без него кнопка «Разбор» будет пустой, остальной кабинет работает.
Встроенный классификатор этого сервиса проще: он только читает проекты и готовит черновик.

## Чего здесь нет

- Голосового Telegram-бота и наших скриптов выкатки.

## Откуда классификатор берёт проекты

- **Мионика** (`MionikaProvider`), если у пользователя заданы токены: `pipeline admin add-user
  --mionika-token ...` или `pipeline admin sync-mionika-tokens`. Вторая команда читает OAuth-токены
  Мионики из локальных настроек Claude Code и отправляет их на ваш сервер, в базе они лежат
  зашифрованными. Адрес Мионики задан в `MIONIKA_MCP_URL` в провайдере.
- **Файл** (`FileProvider`), если Мионика не настроена: `PIPELINE_PROJECTS_FILE`.
- Свой источник подключается классом по протоколу из `server/app/lib/context_providers/base.py`.

## Как развернуть

Нужен сервер на Ubuntu 24.04 с диском под записи, домен и Python 3.12. Для записи Телемоста ещё
Xvfb, PulseAudio и Chromium. Команды ниже — схема по нашей рабочей установке. С нуля по этой
инструкции сервис пока не разворачивали, так что мелочи могут потребовать правки.

1. Положите код в `/opt/call-pipeline`, создайте системного пользователя `pipeline`.
2. Установите сервер:
   ```bash
   apt-get install -y python3.12-venv ffmpeg nginx certbot
   cd /opt/call-pipeline/server
   python3.12 -m venv .venv
   .venv/bin/pip install -e .
   .venv/bin/pip install "speechbrain<1.1"   # более новая версия ломает pyannote 4
   .venv/bin/playwright install chromium     # только для записи Телемоста
   ```
3. Скопируйте `deploy/env.example` в `/etc/call-pipeline/env` и заполните. Комментарии в файле
   подсказывают, как сгенерировать ключи. Создайте `/etc/call-pipeline/projects.md`, по строке на
   проект: `ключ — название — о чём`.
4. Подключите nginx и сертификат. `deploy/nginx.conf.template` проксирует API, вместо `{{SUBDOMAIN}}`
   подставьте свой домен. `deploy/nginx-web.conf` добавляет кабинет. Порт 8443 в шаблоне можно
   заменить на 443.
5. Скопируйте `deploy/*.service` и `*.timer` в `/etc/systemd/system`, включите нужные юниты:
   ```bash
   systemctl daemon-reload
   systemctl enable --now pipeline-api pipeline-worker-calls pipeline-worker-shorts \
     pipeline-worker-classifier pipeline-disk-monitor.timer
   # запись Телемоста:
   systemctl enable --now pipeline-worker-recorder pipeline-telemost-bot
   ```
   Юнит `pipeline-telemost-bot` без `TELEMOST_BOT_TOKEN` не стартует: этот бот нужен для запуска
   записи из Telegram. Из кабинета запись работает через `pipeline-worker-recorder`.
6. Соберите кабинет: `cd web && npm ci && npm run build`, положите `web/dist` в
   `/var/www/call-pipeline-web`.
7. Заведите пользователя:
   ```bash
   curl -X POST "https://<домен>:8443/admin/users" -H "X-Admin-Token: <PIPELINE_ADMIN_TOKEN>" \
     -H "Content-Type: application/json" -d '{"name":"Ирина"}'
   ```
8. Поставьте CLI на ноутбук: `pip install ./cli`. Адрес и токен — в разделе `[server]` файла
   `config.toml` (`url`, `bearer_token`, `user_id`). Проверка: `pipeline upload запись.m4a`.

## Тесты

```bash
cd server && pip install -e ".[dev]" && pytest
cd cli && pip install -e ".[dev]" && pytest
```

## Перед запуском

- **Предупреждайте участников о записи.** Бот входит в Телемост заметным участником, но согласие на
  запись разговора — ваша ответственность.
- Записи и расшифровки лежат на вашем сервере, а текст звонка уходит в Anthropic, если включён
  классификатор, и в Groq или OpenAI, если включены они.
- Запись Телемоста работает через автоматизацию браузера. Если Яндекс изменит интерфейс, вход бота
  может сломаться.

## Лицензия

MIT — см. `LICENSE`.
