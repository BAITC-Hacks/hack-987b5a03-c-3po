# Серверная часть AML Agent

Детерминированная аналитика, хранилище, контролируемые tools, независимая проверка и agent orchestration для AML Agent.

Реализованные модули:

- `aml_agent.analytics`: проверенная загрузка parquet, признаки направленного графа, детерминированные роли, Louvain-кластеры, ранжирование и побайтово стабильный CSV-экспорт;
- `aml_agent.storage`: SQLite repositories для состояния и аудита, а также контролируемые write-once артефакты;
- `aml_agent.tools`: строгие схемы и allowlist состояний для всех десяти tools агента;
- `aml_agent.tool_runtime`: детерминированное выполнение, создание локального review case, экспорт и независимая проверка.
- `aml_agent.agent`: ограниченный demo/live orchestrator, использующий рабочие tools и SQLite-аудит.

Запуск агента на встроенном датасете без API-ключа:

```powershell
.\.venv\Scripts\aml-agent-run.exe --mode demo --data data --database var\agent.sqlite3 --artifacts artifacts
```

Для live-режима установите `backend[live]`, задайте `OPENAI_API_KEY` в окружении или игнорируемом корневом `.env` и запустите `aml-agent-run --mode live` с теми же параметрами данных и хранилища.

Запуск полного offline workflow из корня репозитория:

```powershell
.\.venv\Scripts\aml-agent-tools.exe --data data --database var\aml-agent.sqlite3 --artifacts artifacts --top 20
```

Запуск только аналитического экспорта фазы 1:

```powershell
.\.venv\Scripts\aml-agent-pipeline.exe --data data --out out --top 20 --expected-seeds 81 --period-start 2026-07-01 --period-end 2026-07-31
```

Проверки качества:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m ruff check backend
```

Публичное описание продукта и настройка окружения находятся в корневом `README.md`.
