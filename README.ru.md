🇷🇺 Русский · [🇬🇧 English](README.md)

# Yandex MCP

MCP-сервер для Яндекс Директа, Метрики, Search API, Wordstat и Вебмастера. Даёт AI-ассистенту доступ к рекламным данным, статистике сайта, поисковой выдаче и спросу по запросам.

## Что умеет

- Работа с поддерживаемыми операциями API Директа, Метрики и Вебмастера.
- Создание поисковых заданий через Yandex Cloud Search API и получение результатов.
- Данные Wordstat по запросам, динамике спроса, распределению по регионам и дереву регионов.

## Установка

Нужны Git, [uv](https://docs.astral.sh/uv/getting-started/installation/) и Python 3.12–3.14.

У каждой группы API свои учётные данные. Настройщик начинает с Метрики; для Директа, Вебмастера и Yandex Cloud добавьте доступы к нужным сервисам. Для Cloud Search также нужен ID каталога. См. [настройку сервисов](INSTALL.md#from-a-clone-or-source-zip).

```sh
git clone https://github.com/zai-one/yandex-mcp.git
cd yandex-mcp
uv sync --frozen --extra standalone
uv run --frozen --extra standalone python scripts/configure.py
uv run --frozen --extra standalone yandex-mcp --config mcp.local.json --check-config
uv run --frozen --extra standalone yandex-mcp --config mcp.local.json
```

Настройщик сохраняет данные только локально. `--check-config` проверяет локальную
конфигурацию без запросов к провайдеру. Последняя команда ждёт подключения MCP-клиента.

Инструкции подключения и установки пакета: [INSTALL.md](INSTALL.md).

## Возможности и ограничения

Для поисковых заданий нужен отдельный worker. Запросы Wordstat включаются явно и требуют настройки лимитов расходов. Интерактивная настройка OAuth-входа и экспорт Metrika Logs не реализованы. Подробнее: [конфигурация](docs/RUNTIME.md).

## Обратная связь и лицензия

Пользуйтесь и ставьте ⭐, если проект помогает. Ошибки и пожелания присылайте через
[Issues](https://github.com/zai-one/yandex-mcp/issues/new/choose). Я работаю над проектом
и вношу принятые доработки здесь; поддержка не гарантируется.

Лицензия [LicenseRef-ZAI-ONE](LICENSE) разрешает установку и использование для своих
аккаунтов. Она не является открытой лицензией и не разрешает распространение
производных продуктов. Права сторонних компонентов сохранены в [NOTICE](NOTICE).
Токены, сессии, ключи и данные аккаунтов в Issues не отправляйте.
