# Модель данных AML Agent

## 1. Правило идентификаторов

В исходных parquet- и CSV-файлах GID хранится как `int64`. В API, JSON, текстовых колонках SQLite, frontend types и аргументах tools GID всегда представлен десятичной строкой, потому что значения превышают безопасный целочисленный диапазон JavaScript.

## 2. Входные записи

Пользовательский CSV-импорт принимает одну или несколько таблиц транзакций с
точными колонками `src,dst,date,sum_kzt` и отдельный непустой список seed-GID.
GID сначала читаются как строки и проверяются до преобразования в `int64`.
Backend объединяет CSV, вычисляет минимальную направленную глубину до четырёх
переходов и создаёт три канонические таблицы ниже. Без `transaction_id` полные
дубликаты строк сохраняются с предупреждением: безопасно отличить повторную
загрузку от двух одинаковых переводов невозможно.

### Входная запись `NodeInput`

| Поле | Тип | Ограничение |
|---|---|---|
| `gid` | int64 | Уникален в `nodes.parquet` |
| `depth` | int | От 0 до 4 |
| `is_seed` | bool | Ровно 81 значение true в предоставленном датасете |

### Входная запись `EdgeInput`

| Поле | Тип | Ограничение |
|---|---|---|
| `src` | int64 | Присутствует в nodes |
| `dst` | int64 | Присутствует в nodes |
| `sum_kzt` | float64 | Не меньше 5 000, конечное и положительное значение |
| `n_tx` | int64 | Положительное значение |
| `depth` | int8 | От 1 до 4 |

Пара `(src, dst)` уникальна, потому что рёбра агрегированы за весь период.

### Входная запись `TransactionInput`

| Поле | Тип | Ограничение |
|---|---|---|
| `src` | int64 | Присутствует в nodes |
| `dst` | int64 | Присутствует в nodes |
| `date` | date | В пределах периода из доверенного registry датасетов |
| `sum_kzt` | float64 | Не меньше 5 000, конечное и положительное значение |

Транзакции, сгруппированные по `(src, dst)`, должны воспроизводить `edges.sum_kzt` с точностью 0,01 KZT и точное значение `edges.n_tx`.

## 3. Производные аналитические записи

### Запись признаков `NodeFeature`

| Поле | Тип | Значение |
|---|---|---|
| `run_id` | UUID | Run, которому принадлежит анализ |
| `gid` | string | Безопасный для API идентификатор клиента |
| `depth` | int | Минимальная наблюдаемая глубина обхода |
| `is_seed` | bool | Флаг seed |
| `in_degree` / `out_degree` | int | Уникальные контрагенты |
| `in_kzt` / `out_kzt` | float | Наблюдаемые суммы внутри выборки |
| `in_tx` / `out_tx` | int | Наблюдаемое количество транзакций |
| `pass_through_ratio` | float или null | `out_kzt / in_kzt`; null, если показатель неприменим |
| `pagerank` | float | Направленный PageRank со взвешиванием по сумме |
| `betweenness` | float | Детерминированно выбранный приближённый невзвешенный направленный betweenness |
| `seed_reach_count` | int | Число seed-узлов, из которых существует направленный путь к узлу |
| `rapid_outflow_ratio` | float или null | Доля outflow в течение двух календарных дней после любого наблюдаемого inflow |
| `truncated_by_depth` | bool | `depth=4` без видимого исходящего ребра |
| `uncertainty_flags` | string array | Явные ограничения данных для узла |

Временной сигнал является только вспомогательным evidence, потому что транзакции имеют даты, но не timestamps.

### Оценка узла `NodeAssessment`

| Поле | Тип | Ограничение |
|---|---|---|
| `run_id` | UUID | Foreign key на run |
| `gid` | string | Уникален внутри run |
| `role` | enum | `consolidator`, `transit`, `distributor`, `terminal`, `coordinator`, `peripheral` |
| `role_score` | float | От 0 до 1; сила правила, а не вероятность виновности |
| `cluster_id` | int | Обязателен для каждого узла |
| `priority_score` | float | От 0 до 1 |
| `evidence` | string | Непустая строка до 200 символов с рассчитанными значениями |
| `ruleset_version` | string | Изначально `v1` |

Приоритет ролей для `v1`: `coordinator`, `consolidator`, `distributor`, `transit`, `terminal`, затем `peripheral`. Усечение на границе запрещает назначать terminal только из-за отсутствующего outflow.

### Оценка кластера `ClusterAssessment`

| Поле | Тип | Ограничение |
|---|---|---|
| `run_id` | UUID | Владеющий run |
| `cluster_id` | int | Уникален внутри run |
| `n_nodes` | int | Положительное значение |
| `n_seed` | int | Неотрицательное значение |
| `sum_kzt_internal` | float | Неотрицательное значение |
| `top_gids` | string array | Ранжированные идентификаторы |
| `hypothesis` | string | Осторожное описание на основе evidence |
| `algorithm` | string | `louvain` для MVP |
| `random_seed` | int | Зафиксирован как 42 для воспроизводимости |

### Ранжированная цель `RankedTarget`

| Поле | Тип | Ограничение |
|---|---|---|
| `rank` | int | Начинается с 1, без пропусков |
| `gid` | string | Уникален в списке |
| `role` | role enum | Копируется из assessment |
| `priority_score` | float | Порядок по убыванию |
| `why` | string | Числовое осторожное объяснение |

## 4. Операционные записи

### Аналитический запуск `AnalysisRun`

| Поле | Тип | Значение |
|---|---|---|
| `run_id` | UUID | Первичный ключ |
| `dataset_id` | string | Логическая ссылка на датасет, не путь |
| `dataset_sha256` | string | Fingerprint для воспроизводимости |
| `mode` | enum | `demo` или `live` |
| `status` | enum | Значение state machine |
| `ruleset_version` | string | Версия аналитических правил |
| `model` | string или null | Модель OpenAI в live mode |
| `created_at` / `updated_at` | datetime | Метки времени UTC |
| `warning_count` | int | Число сохранённых предупреждений |
| `verification_status` | enum | `pending`, `passed` или `failed` |

Состояния run:

```text
created -> validated -> graph_ready -> analyzed -> clustered -> classified
        -> ranked -> case_created -> exported -> verified -> completed

Любое незавершённое состояние может перейти в failed; ranked/case_created/exported
могут перейти в verification_failed. Завершённый run и его аналитические
snapshots, результаты tools, events и содержимое артефактов неизменяемы.
```

### Событие агента `AgentEvent`

| Поле | Тип | Значение |
|---|---|---|
| `event_id` | UUID | Первичный ключ |
| `run_id` | UUID | Владеющий run |
| `sequence` | int | Монотонно возрастает внутри run |
| `kind` | enum | `started`, `tool_started`, `tool_completed`, `decision`, `action`, `warning`, `verification`, `failed`, `completed` |
| `tool_name` | string или null | Только зарегистрированный tool |
| `summary` | string | Безопасный UI trace без chain-of-thought |
| `payload_json` | JSON | Очищенные структурированные metadata |
| `created_at` | datetime | Метка времени UTC |

### Проверочный кейс `ReviewCase`

| Поле | Тип | Значение |
|---|---|---|
| `case_id` | UUID | Первичный ключ |
| `run_id` | UUID | Исходный run, уникален для MVP |
| `title` | string | Одна из фиксированных осторожных меток, утверждённых сервером |
| `status` | enum | `ready_for_review`, `in_review`, `closed` |
| `target_gids` | string array | Неизменяемый snapshot целей |
| `created_by` | enum | `agent` или `analyst` |
| `created_at` | datetime | Метка времени UTC |

### Артефакт `Artifact`

| Поле | Тип | Значение |
|---|---|---|
| `artifact_id` | UUID | Первичный ключ |
| `run_id` | UUID | Владеющий run |
| `name` | enum | `nodes_roles.csv`, `clusters.csv`, `top_nodes.csv`, `aml_review_report.xlsx`, `audit.json` |
| `relative_path` | string | Путь внутри директории артефактов run |
| `sha256` | string | Fingerprint целостности |
| `row_count` | int или null | Значение проверки CSV |

## 5. Контракты CSV

### Файл `nodes_roles.csv`

`gid,role,role_score,cluster_id,priority_score,evidence`

Ровно 2 248 строк для предоставленного датасета. Дополнительные аналитические колонки можно добавлять, но обязательные колонки нельзя переименовывать или удалять.

### Файл `clusters.csv`

`cluster_id,n_nodes,n_seed,sum_kzt_internal,top_gids,hypothesis`

### Файл `top_nodes.csv`

`rank,gid,role,priority_score,why`

Не менее 20 строк по убыванию priority.

## 5.1 Человекочитаемый Excel-отчёт

`aml_review_report.xlsx` дублирует проверенные результаты в формате для ручного
анализа. Он не заменяет три обязательных CSV и не является источником
authoritative metrics. В книге есть листы `Priority queue`, `Node assessments`,
`Clusters` и `Run audit`; GID записываются как текст, заголовки закреплены,
фильтры включены, длинные evidence и hypothesis переносятся, а ширина колонок
задана при экспорте.

## 6. Основные инварианты

1. Run нельзя завершить без успешной verification.
2. Каждый входной узел имеет ровно один NodeAssessment и один cluster ID.
3. Текст модели не может изменить аналитическое значение.
4. Evidence воспроизводится из сохранённых признаков и версии ruleset.
5. Изолированные seed остаются в результатах и получают документированную оценку с низким evidence.
6. Повторный запуск одного датасета и ruleset создаёт одинаковые аналитические результаты.
7. Verification заново рассчитывает authoritative roles, scores, evidence, clusters и ranking по зарегистрированным входным данным, не доверяя экспортированным значениям.
