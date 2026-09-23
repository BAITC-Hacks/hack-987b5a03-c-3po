# AML Agent

AML Agent превращает обезличенный граф банковских переводов в объяснимую очередь проверок для AML-аналитика. Система проверяет входные данные, рассчитывает детерминированные графовые признаки, назначает роли, ранжирует цели, создаёт локальный review case и независимо проверяет полученные артефакты.

> Текущий статус: работают детерминированная аналитика, CLI agent и FastAPI на встроенном датасете (фазы 0–4). React UI и Docker Compose ещё не реализованы; состояние задач отражено в [TODO.md](TODO.md).

## Запуск HTTP API и demo

Из корня репозитория, с Python 3.12+ (macOS/Linux):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.lock
python -m pip install --no-deps --no-build-isolation -e backend
python -m pip install -r backend/requirements-api.txt
python -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Windows PowerShell, без активации окружения:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e backend
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-api.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Откройте [интерактивную документацию API](http://127.0.0.1:8000/docs) или [health](http://127.0.0.1:8000/health).
`/health` показывает доступность backend. В `/docs` создайте run через `POST /api/runs` с `{"dataset_id":"bundled","mode":"demo"}`, затем передайте полученный `run_id` в `POST /api/runs/{run_id}/execute`. Дождитесь `status=completed` и `verification_status=passed` в `GET /api/runs/{run_id}`; только после этого доступны оценки узлов, case и проверенные файлы. API принимает только встроенный `dataset_id=bundled`, без загрузки произвольных parquet. В demo-режиме API-ключ не нужен. Используйте один Uvicorn worker: координация выполняющихся запусков находится в памяти процесса.

```bash
python -m pip install -r backend/requirements-api-dev.txt
python -m pytest backend/tests/api -q
python -m ruff check backend/app/api backend/app/config.py backend/app/main.py backend/tests/api
```

Тесты `backend/tests/api/` проверяют HTTP-контракт и полный golden path на настоящем backend. Зависимости HTTP зафиксированы отдельно от `backend/requirements.lock`. Для полного набора установите также `backend/requirements-api-dev.txt`, затем запустите `python -m pytest backend/tests -q` в активированном окружении (Windows без активации: `.\.venv\Scripts\python.exe -X utf8 -m pytest backend\tests -q`).

## Проблема

AML-аналитик начинает с 81 известного seed-клиента, но должен вручную исследовать четырёхуровневую сеть из 2 248 счетов. Полезный результат — не общее резюме, а ответ на три конкретных вопроса: **какие счета проверять первыми, почему и какие данные подтверждают этот приоритет**.

AML Agent — инструмент поддержки решений. Роли и приоритеты являются гипотезами для проверки аналитиком, а не доказательствами вины и не указаниями блокировать счёт.

## Целевой workflow

```text
Пакет parquet
  -> проверка данных и ограничений
  -> построение направленного взвешенного графа
  -> расчёт структурных и временных признаков
  -> кластеризация и назначение объяснимых ролей
  -> ранжирование целей для проверки
  -> создание локального AML review case
  -> экспорт и проверка обязательных артефактов
```

В live-режиме модель OpenAI выбирает контролируемые tools и возвращает конечное решение по строгой схеме. Детерминированный backend рассчитывает роли и метрики, создаёт case и проверяет результаты.

## Реализованный golden path через CLI

Текущий offline workflow выполняет девять изменяющих состояние или проверочных шагов. Десятый контролируемый tool, `get_node_evidence`, вызывается по запросу:

```text
inspect_dataset -> build_graph -> compute_graph_features -> cluster_network
-> assign_roles -> rank_targets -> create_review_case -> export_results
-> verify_run -> completed
```

На встроенном датасете система создаёт 2 248 оценок узлов, 91 сводку по кластерам, top-20, один локальный review case, три обязательных CSV-файла, `audit.json` и отчёт из 19 проверок. Demo-режиму API-ключ не нужен.

### Быстрый запуск в Windows

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e backend
.\.venv\Scripts\aml-agent-tools.exe --data data --database var\aml-agent.sqlite3 --artifacts artifacts --top 20
.\.venv\Scripts\aml-agent-run.exe --mode demo --data data --database var\agent.sqlite3 --artifacts artifacts
```

### Быстрый запуск в macOS/Linux

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e backend
.venv/bin/aml-agent-tools --data data --database var/aml-agent.sqlite3 --artifacts artifacts --top 20
.venv/bin/aml-agent-run --mode demo --data data --database var/agent.sqlite3 --artifacts artifacts
```

Успешный agent run возвращает `status: completed`; сохранённый run имеет `verification_status: passed`, а результаты всех девяти tools содержат `ok: true`. Каждый запуск создаёт новую запись run.

## Датасет

В репозитории находится обезличенный датасет хакатона:

- `data/nodes.parquet`: 2 248 клиентов;
- `data/edges.parquet`: 3 119 агрегированных направленных рёбер;
- `data/transactions.parquet`: 4 840 отдельных транзакций;
- период: с 2026-07-01 по 2026-07-31;
- наблюдаемый оборот: 365 890 012,01 KZT.

Поля и ограничения сбора описаны в [data/README.md](data/README.md). Главное ограничение — граница обхода в четыре перехода: у 444 узлов с `depth=4` нет видимых исходящих переводов, поэтому их нельзя автоматически считать конечными получателями.

## Архитектура

```text
React / Vite UI (фаза 5)
       |
       v
FastAPI-приложение (фаза 4) ---- SQLite-хранилище run/case/audit [реализовано]
       |
       +---- Agent orchestrator [реализовано] ---- OpenAI Responses API
       |              |
       |              +---- строгие function tools [реализовано]
       |
       +---- Детерминированный analytics engine [реализовано]
                     |
                     +---- pandas / NetworkX / SciPy
                     +---- parquet на входе
                     +---- CSV- и JSON-артефакты
```

Подробная документация:

- [Архитектура](docs/ARCHITECTURE.md)
- [Baseline фазы 0](docs/BASELINE.md)
- [Результаты проверки фаз 0–2](docs/PHASES_0_2_RESULTS.md)
- [Модель данных](docs/DATA_MODEL.md)
- [Аналитические правила](docs/ANALYTICS.md)
- [Контракты tools](docs/TOOLS.md)
- [Agent loop](docs/AGENT_LOOP.md)
- [HTTP API](docs/API.md)
- [План реализации](TODO.md)

## Настройка OpenAI

Live orchestrator использует OpenAI Responses API с function calling и Structured Outputs. Модель задаётся через `OPENAI_MODEL` и не зашивается в аналитику или tools. Для `--mode live` установите дополнительные зависимости:

```bash
.venv/bin/python -m pip install -e 'backend[live]'
.venv/bin/aml-agent-run --mode live --data data --database var/live.sqlite3 --artifacts artifacts
```

1. Скопируйте `.env.example` в `.env`, если локального файла ещё нет.
2. Добавьте существующий ключ только в локальный игнорируемый файл:

   ```dotenv
   OPENAI_API_KEY=your-existing-key
   DEMO_MODE=false
   ```

3. Запустите live-команду из корня репозитория, чтобы CLI загрузил игнорируемый `.env`. Никогда не добавляйте ключ в исходный код, логи или коммиты.

CLI по умолчанию запускается с `--mode demo`: графовая аналитика остаётся настоящей, заменяется только внешний model provider.

Автоматические тесты используют имитацию транспорта Responses для проверки live-provider и не отправляют запросы в OpenAI API.

Официальная документация: [function calling](https://developers.openai.com/api/docs/guides/function-calling), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) и [`gpt-5-mini`](https://developers.openai.com/api/docs/models/gpt-5-mini).

## Запуск стартового решения

Starter проверяет parquet-файлы, строит граф, рассчитывает базовые признаки и записывает пустые шаблоны результатов. Это baseline, а не готовый продукт.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r starter/requirements.txt
.venv/bin/python starter/starter.py --data data --out out
```

В Windows замените `.venv/bin/python` на `.\.venv\Scripts\python.exe`.

Ожидаемые файлы:

- `out/nodes_roles.csv`
- `out/clusters.csv`
- `out/top_nodes.csv`

Starter намеренно не реализует назначение ролей, кластеризацию, ранжирование и визуализацию.

## Компонент orchestration фазы 3

`backend/aml_agent/agent/` содержит ограниченный цикл запуска, demo-provider, адаптер OpenAI Responses, строгую проверку вызовов tools и адаптеры к рабочим SQLite-аудиту и runtime tools. Demo-режим выполняет настоящую аналитику и создание case. Интеграционный тест сравнивает результаты demo- и live-provider на встроенных parquet-файлах; транспорт Responses в тесте имитируется.

Запуск тестов:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
```

Для live-режима требуются дополнительные зависимости `backend[live]` и `OPENAI_API_KEY` в окружении или игнорируемом `.env`.

## Обязательные итоговые артефакты

- `nodes_roles.csv`: одна строка для каждого из 2 248 узлов;
- `clusters.csv`: статистика кластеров и осторожная гипотеза;
- `top_nodes.csv`: минимум 20 ранжированных целей для проверки;
- браузерный UI с направлением графа, ролями, кластерами, поиском GID, execution trace и состоянием до/после;
- запись аудита с хешем датасета, версией ruleset, событиями tools, предупреждениями и результатом проверки.

## Безопасность и объяснимость

- В JSON значения GID передаются строками, потому что они превышают безопасный целочисленный диапазон JavaScript. В CSV/parquet сохраняется `int64`.
- Входящие переводы seed-клиентов неполны, поэтому их коэффициент pass-through нельзя использовать без оговорок.
- Узел с `depth=4` без видимого исходящего ребра получает флаг `truncated_by_depth`, а не автоматически роль `terminal`.
- `role_score` означает силу совпадения с правилом, а не вероятность преступной деятельности.
- Каждая строка evidence должна ссылаться на рассчитанные значения и занимать не более 200 символов.
- Внешние и разрушительные действия не входят в MVP. Единственное записывающее действие — создание локального review case и пакета экспорта.

## Структура проекта

```text
.
|-- AGENTS.md
|-- TODO.md
|-- data/
|   |-- README.md
|   |-- edges.parquet
|   |-- nodes.parquet
|   `-- transactions.parquet
|-- docs/
|   |-- AGENT_LOOP.md
|   |-- ANALYTICS.md
|   |-- ARCHITECTURE.md
|   |-- API.md
|   |-- DATA_MODEL.md
|   `-- TOOLS.md
|-- backend/
|   |-- app/api/          # Phase 4 HTTP routes, DTOs, SSE, adapters
|   |-- app/config.py
|   |-- app/main.py
|   |-- aml_agent/
|   |   |-- agent/
|   |   |-- analytics/
|   |   |-- storage/
|   |   |-- tools/
|   |   `-- tool_runtime.py
|   |-- tests/            # Phase 1–3 tests and Phase 4 tests/api/
|   |-- pyproject.toml
|   |-- requirements.lock
|   |-- requirements-api.txt
|   `-- requirements-api-dev.txt
|-- starter/
|   |-- README.md
|   |-- requirements.txt
|   `-- starter.py
|-- .env.example
`-- README.md
```

## Границы MVP

В hackathon MVP не входят автоматическая блокировка счетов, отправка данных регулятору, внешнее обогащение, выполнение произвольного кода, generic chat, multi-agent orchestration и production-обработка графа из миллиона узлов.

Для будущего развёртывания на миллионе узлов слой NetworkX следует заменить графовым движком или распределённой аналитической базой данных, сохранив контракты tools и API.
