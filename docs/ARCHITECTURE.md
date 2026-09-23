# Архитектура AML Agent

## 1. Цель

Построить один узкий end-to-end продукт: преобразовать предоставленный пакет parquet в проверенный AML review case для аналитика. Система должна сохранять полезность без OpenAI API: AI координирует и объясняет, а все расчёты и проверки принадлежат детерминированному коду.

## 2. Архитектурные решения

| Решение | Выбор | Причина |
|---|---|---|
| Топология агента | Один orchestrator с контролируемыми tools | Не создавать искусственную multi-agent сложность |
| AI-интеграция | OpenAI Responses API | Нативный итеративный function-calling loop |
| Live model | Настраиваемая, по умолчанию `gpt-5-mini` | Низкая latency/стоимость и поддержка нужных API-возможностей |
| Offline mode | Детерминированный provider с той же state machine tools | Надёжная проверка судьями без API-ключа |
| Analytics | pandas + NetworkX + NumPy/SciPy | Более чем достаточно для 2 248 узлов |
| Backend | Python 3.12 + FastAPI + Pydantic | Единый стек с аналитикой и строгие схемы |
| Frontend | React + Vite + TypeScript + Cytoscape.js | Быстрая разработка UI и сфокусированные представления графа |
| Storage | SQLite и файловые артефакты | Воспроизводимое локальное состояние без инфраструктуры |
| Deployment | Docker Compose | Локальный запуск одной командой |

## 3. Компоненты

```text
Браузер
  React/Vite
  - управление run
  - безопасный execution trace
  - таблица приоритетов
  - представления кластера и ego graph
  - состояние case до/после
       |
       | HTTP + Server-Sent Events
       v
FastAPI
  - проверяет запросы
  - передаёт GID строками
  - предоставляет endpoints run/case/artifact
       |
       +--------------------+
       |                    |
       v                    v
Agent orchestrator       Query service
  - state machine          - срезы графа
  - allowed tools          - карточки узлов
  - tool budget            - сводки кластеров
  - retry policy
       |
       +--------------------+
       |                    |
       v                    v
Provider adapter        Tool registry
  OpenAI Responses        - проверка датасета
  Deterministic demo      - графовая аналитика
                          - правила roles/rank
                          - case/export/verify
                               |
             +-----------------+-----------------+
             v                                   v
        SQLite store                      Artifact store
        runs, events, cases               CSV, JSON audit
```

## 4. Структура репозитория

```text
backend/
  aml_agent/
    analytics/            # граф, признаки, роли, ranking, кластеры
    storage/              # SQLite repositories и пути артефактов
    tools/                # строгие схемы и state allowlist
    tool_runtime.py       # детерминированное выполнение tools фазы 2
    cli.py
  tests/
  pyproject.toml
  requirements.lock
data/
starter/
docs/
```

Фазы 3–5 добавляют модули provider/orchestrator, FastAPI routes и приложение `frontend/`, не перемещая уже реализованные границы analytics и storage.

## 5. Правила зависимостей

1. `analytics` — чистый детерминированный Python без зависимостей от OpenAI, HTTP, UI или базы данных.
2. `tools` могут вызывать analytics и repositories, но не произвольные shell-команды.
3. `agent` вызывает только зарегистрированные tools и не имеет прямого доступа к pandas dataframes, путям файлов или SQL.
4. `api` вызывает application services, а не NetworkX напрямую.
5. `frontend` получает GID строками и не выполняет authoritative расчёты scores.
6. OpenAI output может выбирать tools и давать осторожные формулировки, но не может переписывать рассчитанные metrics, roles, scores или verification status.

## 6. Runtime flow

1. `POST /api/runs` создаёт run для встроенного датасета или загруженного проверенного пакета.
2. `POST /api/runs/{run_id}/execute` запускает orchestrator.
3. Events tools сохраняются до передачи через SSE в `GET /api/runs/{run_id}/events`.
4. Аналитические артефакты записываются в `artifacts/{run_id}/` атомарно: сначала временный файл, затем rename.
5. `create_review_case` сохраняет top targets и изменяет видимое состояние до/после.
6. `verify_run` заново загружает зарегистрированные parquet, пересчитывает authoritative analytics и сравнивает файлы независимо от агента и in-memory cache.
7. Только проверенный run может перейти в `completed` или открыть финальные downloads.

## 7. API surface

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/health` | Health контейнера и режим без секретов |
| `POST` | `/api/runs` | Создать run по известному датасету |
| `POST` | `/api/runs/{run_id}/execute` | Запустить или безопасно продолжить выполнение |
| `GET` | `/api/runs/{run_id}` | Status run, счётчики, warnings и итоговый результат |
| `GET` | `/api/runs/{run_id}/events` | SSE execution trace |
| `GET` | `/api/runs/{run_id}/nodes` | Пагинируемый и фильтруемый priority list |
| `GET` | `/api/runs/{run_id}/nodes/{gid}` | Evidence узла и ego graph |
| `GET` | `/api/runs/{run_id}/clusters` | Сводки кластеров |
| `GET` | `/api/cases/{case_id}` | Review case и snapshot целей |
| `GET` | `/api/runs/{run_id}/artifacts/{name}` | Скачивание артефакта из allowlist |
| `POST` | `/api/demo/reset` | Сброс только состояния, созданного demo |

## 8. Граница OpenAI

Live provider отправляет в Responses API компактную сводку состояния run и разрешённые сейчас строгие function tools. Сырые таблицы транзакций и полные выгрузки графа не передаются. Outputs tools содержат только агрегаты и evidence, необходимые для следующего решения.

Конечный ответ модели соответствует строгой схеме `AgentDecision`:

```json
{
  "status": "completed",
  "run_id": "uuid",
  "case_id": "uuid",
  "summary": "Review case created and verified.",
  "warnings": ["Depth-4 recipients remain boundary-limited."],
  "recommended_next_step": "Analyst reviews the top 20 targets."
}
```

Hidden reasoning не сохраняется и не показывается. Audit содержит вызовы tools, очищенные inputs, summaries, warnings, actions и результаты verification.

## 9. Политика надёжности

- Максимум 12 вызовов tools на run по умолчанию.
- Максимум 2 попытки для повторяемого tool.
- Timeout OpenAI: 30 секунд, затем безопасное продолжение детерминированным provider, если это возможно.
- Операции tools идемпотентны по `(run_id, tool_name, ruleset_version)`.
- `create_review_case` использует idempotency key, полученный из run и snapshot ранжированных целей.
- Неудачная verification переводит run в `verification_failed` и никогда не показывает успешное состояние.
- Невалидный ответ модели отклоняется проверкой схемы и повторяется один раз до deterministic fallback.

## 10. Граница безопасности

- `OPENAI_API_KEY` читается только из environment configuration и никогда не возвращается через `/health` или логи.
- File tools принимают `dataset_id` и `run_id`, а не произвольные пути.
- Артефакты для скачивания выбираются из фиксированного allowlist.
- Ни один tool не выполняет shell-команды или произвольный Python от модели.
- Внешние действия и блокировка счетов не входят в scope.

## 11. Масштабирование

При размере около миллиона узлов нужно сохранить API-, tool- и domain-контракты, но заменить in-memory вычисления NetworkX на igraph, graph-tool, Spark GraphFrames либо графовую базу или аналитическое хранилище. Таблицы признаков должны стать columnar, а визуализация графа должна оставаться отфильтрованной на сервере вместо отправки всей сети в браузер.
