# AML Agent — инструкции для AI-инженеров

========================
РОЛЬ
========================

Ты работаешь над AML Agent — специализированным инструментом для первичного AML-триажа транзакционной сети.

Главный вопрос проекта:

> Что агент сделал для аналитика, а не что агент написал?

Не превращай продукт в chatbot, generic RAG, AI-summary или демонстрацию модели ради модели.

========================
ПРОДУКТОВАЯ ЦЕЛЬ
========================

За один воспроизводимый запуск система должна:

1. Принять три parquet-файла.
2. Проверить их целостность и ограничения.
3. Построить направленный граф переводов.
4. Рассчитать объяснимые признаки.
5. Присвоить роль каждому из 2 248 узлов.
6. Разбить сеть на кластеры.
7. Сформировать top-20 для проверки.
8. Создать локальный AML review case.
9. Экспортировать три обязательных CSV.
10. Независимо проверить результат.

Результат — гипотеза для аналитика. Никогда не утверждай, что клиент виновен или является преступником.

========================
ПРИОРИТЕТЫ
========================

Работай в таком порядке:

1. Детерминированный аналитический pipeline.
2. Проверяемые роли, scores и evidence.
3. Agent tools и orchestration.
4. Review case и verification.
5. Golden-path API.
6. Минимальный UI, показывающий workflow.
7. Docker и воспроизводимость.
8. README и demo polish.
9. Только потом optional features.

Если компонент не усиливает end-to-end demo или must-have ТЗ, подвергай сомнению его необходимость.

========================
SOURCE OF TRUTH
========================

Перед изменениями прочитай релевантные документы:

- `README.md` — продукт и текущий статус;
- `TODO.md` — приоритеты реализации;
- `docs/ARCHITECTURE.md` — границы компонентов;
- `docs/DATA_MODEL.md` — типы и инварианты;
- `docs/TOOLS.md` — точные tool schemas;
- `docs/AGENT_LOOP.md` — state machine и retries;
- `docs/ANALYTICS.md` — ruleset ролей и ranking.

Если реализация требует изменить зафиксированный контракт, сначала обнови документ и объясни причину в commit message или PR.

========================
АРХИТЕКТУРА
========================

Используй минимальный стек:

- frontend: React + Vite + TypeScript;
- backend: Python + FastAPI + Pydantic;
- analytics: pandas + NetworkX + NumPy/SciPy;
- storage: SQLite + локальные artifacts;
- AI: OpenAI Responses API;
- deployment: Docker Compose.

Не добавляй Kubernetes, Kafka, Redis, PostgreSQL, микросервисы или очереди без доказанной необходимости для MVP.

Dependency rules:

- analytics не знает об OpenAI, FastAPI и UI;
- tools вызывают analytics и storage через явные интерфейсы;
- orchestrator вызывает только зарегистрированные tools;
- frontend не рассчитывает authoritative metrics;
- модель не имеет прямого доступа к filesystem, SQL или shell.

========================
AGENT DESIGN
========================

Используй одного основного orchestrator и контролируемые Python tools.

Правильный workflow:

```text
EVENT
-> OBSERVE
-> CHOOSE TOOL
-> EXECUTE
-> DECIDE
-> CREATE REVIEW CASE
-> VERIFY
-> RESULT + AUDIT
```

Не создавай Planner Agent, Reviewer Agent, Thinker Agent и другие искусственные роли.

Agent run считается завершённым только после `verify_run(passed=true)`.

Execution trace показывает только:

- tool started/completed;
- безопасные observations;
- решения верхнего уровня;
- action;
- verification;
- warnings/errors.

Не показывай chain-of-thought, hidden reasoning, системные prompts или необработанные model outputs.

========================
OPENAI
========================

Для live mode используй официальный OpenAI SDK и Responses API.

- Function tools должны быть strict.
- `additionalProperties` всегда `false`.
- Аргументы дополнительно валидируются приложением.
- Финальный ответ модели проходит Structured Outputs schema.
- Tool output возвращается по соответствующему `call_id`.
- Модель выбирается через `OPENAI_MODEL`; не хардкодь её в бизнес-логике.
- API timeout, malformed response и refusal должны обрабатываться.

`OPENAI_API_KEY` хранится только в локальном `.env` или environment. Никогда не печатай, не логируй и не коммить ключ.

`DEMO_MODE=true` не является записанной анимацией. Он заменяет только model provider; analytics, tools, storage, case creation, export и verification остаются настоящими.

========================
АНАЛИТИКА
========================

LLM не рассчитывает:

- graph metrics;
- roles;
- role_score;
- priority_score;
- cluster_id;
- CSV rows;
- verification result.

Эти значения создаёт только детерминированный ruleset из `docs/ANALYTICS.md`.

Каждая роль должна иметь:

- формальное правило;
- числовой score;
- evidence с конкретными значениями;
- uncertainty flags;
- versioned ruleset.

`role_score` — сила совпадения с правилом, не вероятность преступления.

========================
ЛОВУШКИ ДАННЫХ
========================

Обязательно учитывай:

1. 444 узла depth=4 без исходящих — граница обхода, а не доказанный terminal.
2. Входящие суммы seed занижены; их pass-through ненадёжен.
3. 19 seed отсутствуют в рёбрах, но обязаны попасть в итоговый CSV.
4. В данных нет ФИО, ИИН, возраста, дохода и ground truth.
5. Даты не содержат время; порядок переводов внутри дня неизвестен.
6. GID превышает safe integer JavaScript.

Правило GID:

- parquet/CSV: `int64`;
- API/JSON/SQLite/frontend/tool arguments: decimal string;
- никогда не конвертируй GID в JavaScript `number`.

========================
TOOLS И SECURITY
========================

Tools принимают только логические идентификаторы: `run_id`, `dataset_id`, `gid`.

Нельзя принимать от модели:

- произвольный path;
- shell command;
- Python code;
- SQL;
- URL для server-side fetch;
- имя файла вне allowlist.

State-changing tools должны быть идемпотентными.

MVP не выполняет:

- блокировку счетов;
- отправку в регулятор;
- изменение банковских систем;
- внешнее обогащение;
- автоматические обвинительные решения.

Разрешённое действие — создать локальный review case и verified export bundle.

========================
UI
========================

UI продаёт workflow, а не декоративный дизайн.

Покажи:

- что произошло;
- текущий статус run;
- tool execution trace;
- top targets;
- role/evidence/uncertainty;
- cluster overview;
- направленный ego graph;
- before/after review case;
- verification status;
- downloads.

Не пытайся одновременно рисовать все 2 248 узлов как основной экран. Используй cluster view и 1–2-hop ego graph.

========================
QUALITY GATES
========================

После каждого крупного этапа приложение должно оставаться запускаемым.

Минимальные проверки:

- unit tests для role rules и score bounds;
- data consistency test edges ↔ transactions;
- boundary regression test;
- all-nodes coverage test;
- deterministic rerun test;
- tool schema validation;
- state transition tests;
- API health test;
- golden-path integration test;
- demo mode without API key;
- live mode missing-key failure without secret leakage.

Не считай задачу законченной только потому, что UI выглядит готовым.

========================
GIT И ДОКУМЕНТАЦИЯ
========================

- Не коммить `.env`, secrets, generated artifacts, SQLite database, caches и build output.
- Не переписывай рабочие компоненты без необходимости.
- Делай небольшие логические commits.
- Commit message должен описывать результат, например `feat: add deterministic role pipeline`.
- README описывает только то, что реально работает.
- При изменении tool schema, data model или state machine обновляй соответствующий документ.

========================
DEFINITION OF DONE
========================

MVP готов, когда на чистой машине можно выполнить documented command и получить:

- работающий frontend и backend;
- настоящий run по bundled dataset;
- понятный execution trace;
- 2 248 заполненных node assessments;
- clusters.csv;
- top_nodes.csv с минимум 20 строками;
- локальный review case;
- passed verification;
- demo mode без API key;
- live mode с OpenAI API key;
- README, который позволяет понять и запустить проект без команды.
