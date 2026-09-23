# Цикл AML Agent

## 1. Назначение

Orchestrator — ограниченная state machine, а не открытый chat. Его задача — выбрать следующий допустимый tool, обработать результат, создать один локальный review case, потребовать verification и вернуть структурированный результат.

## 2. Входные данные модели

На каждом шаге provider получает:

1. Стабильную system instruction с границами задачи, правилами безопасных формулировок и условием завершения.
2. Компактный `RunContext`: `run_id`, текущее состояние, режим, завершённые tools, счётчики, предупреждения, оставшийся бюджет tools и необходимость подтверждения аналитика.
3. Только function tools, разрешённые в текущем сохранённом состоянии.
4. Предыдущие tool-call items и созданные приложением `function_call_output`, связанные через `call_id` во время текущего запуска.

Содержимое parquet целиком, полные выгрузки графа, секреты, локальные пути, SQL и внутренние stack traces никогда не отправляются модели.
Цепочка Responses хранится только во время текущего выполнения. Возобновлённый run начинает новую цепочку по сохранённому состоянию и ranking snapshot, без хранения transcript модели.

## 3. Автомат состояний

```text
created
  -> inspect_dataset
validated
  -> build_graph
graph_ready
  -> compute_graph_features
analyzed
  -> cluster_network
clustered
  -> assign_roles
classified
  -> rank_targets
ranked
  -> optional get_node_evidence for selected targets
  -> create_review_case
case_created
  -> export_results
exported
  -> verify_run
verified
  -> completed (runtime записывает terminal audit events после успешной проверки)
```

Результат tool меняет состояние только внутри своей транзакции. Модель не может самостоятельно объявить или установить состояние. После успешного `verify_run` рабочий runtime записывает события проверки и завершения, а затем сразу переводит run в `completed`. Orchestrator проверяет конечное решение по сохранённым case и результату verification.

Если на этапе `ranked` установлено `analyst_confirmation_required`, выполнение возвращает `needs_user_action` до создания case.

## 4. Псевдокод цикла

```python
async def execute_run(run_id: UUID) -> AgentDecision:
    used_tool_calls = 0
    previous_response_id = None
    while True:
        context = run_repository.get_compact_context(run_id)

        if context.state in (RunState.VERIFIED, RunState.COMPLETED):
            decision = await provider.final_decision(context, previous_response_id)
            return complete_only_if_verification_passed(context, decision)

        if used_tool_calls >= settings.openai_max_tool_calls:
            return fail(run_id, code="TOOL_BUDGET_EXCEEDED")

        tools = tool_registry.allowed_definitions(context.state)
        response = await provider.respond(
            context=context,
            tools=tools,
            previous_response_id=previous_response_id,
        )
        previous_response_id = response.response_id

        calls = validate_function_calls(response, tools)
        if not calls:
            raise InvalidAgentResponse("A valid next tool is required")

        for call in calls:
            result = await tool_registry.execute(call.name, call.arguments)
            used_tool_calls += 1
            # ToolRuntime сохраняет события tools и verification в SQLite.
            provider.add_tool_output(call_id=call.call_id, output=safe_summary(result))

            if not result.ok and not result.error.retriable:
                return fail_from_tool_result(result)

```

Конечное решение использует Structured Outputs. Если ответ модели после verification невалиден, содержит refusal или превышает timeout, orchestrator берёт фиксированное локальное решение из сохранённого состояния. Формулировка модели не меняет case snapshot, проверки или условие завершения.

За одну итерацию разрешён только один tool, изменяющий состояние. Read-only вызовы `get_node_evidence` можно объединять максимум для трёх целей.

## 5. Контракт системной инструкции

Production system instruction обязана закреплять следующие правила:

- Ты — AML Agent, orchestrator workflow проверки.
- Используй только доступные function tools и соблюдай ограничения текущего состояния.
- Не делай выводы о виновности, личности, профессии, доходе или отсутствующих в данных атрибутах.
- Не рассчитывай и не изменяй графовые метрики самостоятельно.
- Описывай результаты как структурные индикаторы или гипотезы для проверки.
- Явно учитывай неопределённость из-за отсутствующего outflow на `depth=4` и неполного inflow seed-клиентов.
- Создай ровно один локальный review case по сохранённому ranking.
- Выбирай title case только из утверждённого сервером осторожного allowlist; не придумывай обвинительные или криминальные метки.
- Run считается завершённым, только когда `verify_run` возвращает `passed: true`.
- Не раскрывай hidden reasoning. Возвращай только короткие решения и безопасные сводки tools.

## 6. Интерфейс провайдера

```python
class AgentProvider(Protocol):
    async def respond(
        self,
        context: RunContext,
        tools: list[dict],
        previous_response_id: str | None,
    ) -> ProviderResponse: ...
```

### Провайдер `OpenAIResponsesProvider`

- Использует официальный OpenAI Python SDK и Responses API.
- Читает модель и timeout из settings.
- Передаёт строгие function definitions из registry.
- Возвращает нормализованные вызовы с `call_id`, именем tool и проверенными аргументами.
- Использует Structured Outputs для конечного `AgentDecision`.

### Провайдер `DeterministicDemoProvider`

- Использует то же сохранённое состояние и тот же registry tools.
- Выбирает единственный допустимый следующий изменяющий состояние tool по статической политике.
- Использует детерминированные шаблоны для titles, warnings и summaries.
- Не подменяет аналитику, запись case, экспорт, events или verification.

## 7. Схема конечного решения

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string",
      "enum": ["completed", "failed", "needs_user_action"]
    },
    "run_id": {"type": "string", "format": "uuid"},
    "case_id": {
      "type": ["string", "null"],
      "format": "uuid"
    },
    "summary": {"type": "string", "maxLength": 500},
    "warnings": {
      "type": "array",
      "items": {"type": "string", "maxLength": 240},
      "maxItems": 10
    },
    "recommended_next_step": {"type": "string", "maxLength": 300}
  },
  "required": [
    "status",
    "run_id",
    "case_id",
    "summary",
    "warnings",
    "recommended_next_step"
  ],
  "additionalProperties": false
}
```

`case_id` не равен null только после сохранения case. Значение `completed` принимается только при `verification_status=passed` в repository, независимо от текста модели.

## 8. Политика ошибок и повторов

| Ошибка | Поведение |
|---|---|
| Timeout OpenAI | Повторить один раз, затем продолжить с deterministic provider, если текущий переход однозначен |
| Нет API-ключа в live mode | Вернуть `needs_user_action`; никогда не логировать значение environment variable |
| Невалидные аргументы модели | Отклонить до выполнения tool и запросить один исправленный вызов |
| Неповторяемая ошибка данных | Остановить run и показать применимые результаты валидации |
| Повторяемая ошибка analytics/storage | Повторить tool один раз с тем же idempotency key |
| Case уже существует | Вернуть существующий case с `idempotent_replay: true` |
| Ошибка verification | Сохранить failed checks; не помечать run завершённым |
| Бюджет tools исчерпан | Безопасно завершить с ошибкой и сохранить выполненные артефакты для диагностики |

## 9. Политика трассировки в UI

Разрешённое содержимое trace:

```text
Agent started
-> inspect_dataset()
OK 2,248 nodes; edges and transactions reconcile
-> build_graph()
OK 444 depth-boundary nodes flagged
-> assign_roles()
OK 2,248 of 2,248 nodes classified
-> create_review_case()
OK case AML-2026-07-001 created with 20 targets
-> verify_run()
OK all mandatory checks passed
```

Нельзя показывать prompts, hidden reasoning, token-level thoughts, stack traces, секреты и неограниченный raw output tools.

## 10. Критерии завершения основного сценария

Цикл считается успешным только если:

- все 2 248 узлов имеют валидные role, cluster, score и evidence;
- ранжировано не менее 20 целей;
- review case содержит точный snapshot ranking;
- все обязательные файлы существуют и имеют валидные hashes и schemas;
- `verify_run` завершён успешно;
- UI получил событие completed и показывает состояние после выполнения.
