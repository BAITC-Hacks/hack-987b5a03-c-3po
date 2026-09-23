# Аналитический ruleset AML Agent v1

## 1. Принцип

Все authoritative roles, scores, clusters и rankings рассчитываются детерминированно. Модель может суммировать сохранённые evidence, но не может создавать аналитические значения.

## 2. Структурные признаки

- `in_degree`, `out_degree`: число уникальных наблюдаемых контрагентов.
- `in_kzt`, `out_kzt`: наблюдаемый объём внутри выбранного графа.
- `in_tx`, `out_tx`: наблюдаемое количество транзакций.
- `pass_through_ratio = out_kzt / in_kzt`, если наблюдаемый inflow положителен и узел не является seed.
- `pagerank`: направленный PageRank со взвешиванием по `sum_kzt`.
- `betweenness`: приближённый направленный невзвешенный betweenness с детерминированным sample seed 42. Сумма не используется как расстояние.
- `seed_reach_count`: число уникальных seed, из которых достижим узел.
- `rapid_outflow_ratio`: доля outflow, произошедшего в течение от нуля до двух календарных дней после любого наблюдаемого inflow.
- `truncated_by_depth`: узел находится на `depth=4` и не имеет видимого исходящего ребра.

Percentile ranks рассчитываются по всем узлам с методом равенства `average`.

## 3. Поиск сообществ

Louvain применяется к ненаправленной проекции, в которой суммы взаимных рёбер складываются. Эта проекция используется только для поиска сообществ. Направление остаётся authoritative для ролей и evidence.

Параметры MVP:

- resolution: `1.0`;
- random seed: `42`;
- изолированные узлы получают singleton clusters;
- каждый узел получает ровно один `cluster_id`.

## 4. Условия и приоритет ролей

Правила проверяются в следующем порядке, чтобы каждый узел получил одну основную роль.

### coordinator

Роль доступна, если выполняются все условия:

- узел не является seed;
- depth меньше 4;
- есть входящие и исходящие рёбра;
- `seed_reach_count` не ниже 90-го percentile;
- `coordinator_index` не ниже 99-го percentile.

```text
coordinator_index =
    0.30 * betweenness_percentile
  + 0.25 * seed_reach_percentile
  + 0.20 * pagerank_percentile
  + 0.15 * in_degree_percentile
  + 0.10 * out_degree_percentile
```

### consolidator

Роль доступна, если:

- узел не является seed;
- `in_degree >= 5`;
- `pass_through_ratio < 0.5`.

### distributor

Роль доступна, если:

- `out_degree >= 10`;
- `out_degree >= 2 * max(in_degree, 1)`.

Правило не использует pass-through ratio seed-клиентов.

### transit

Роль доступна, если:

- узел не является seed;
- depth меньше 4;
- `in_degree >= 2` и `out_degree >= 1`;
- `0.8 <= pass_through_ratio <= 1.2`.

`rapid_outflow_ratio` повышает уверенность, но не является обязательным: в данных есть только календарные даты без времени.

### terminal

Роль доступна, если:

- узел не является seed;
- depth меньше 4;
- `out_degree == 0`;
- `in_degree >= 2`.

Узел с `depth=4` не может получить эту роль только из-за отсутствия исходящих переводов.

### peripheral

Fallback для узлов, по которым недостаточно evidence для другой роли, включая изолированные seed и ограниченные границей узлы без более сильного наблюдаемого поведения.

## 5. Role score

Каждый компонент ниже ограничивается диапазоном `[0, 1]`.

```text
coordinator = coordinator_index

consolidator =
    0.45 * clip(in_degree / 10)
  + 0.35 * clip((1 - pass_through_ratio) / 0.8)
  + 0.20 * in_kzt_percentile

distributor =
    0.50 * out_degree_percentile
  + 0.30 * clip(out_degree / (2 * max(in_degree, 1)) / 5)
  + 0.20 * out_kzt_percentile

transit =
    0.50 * clip(1 - abs(pass_through_ratio - 1) / 0.2)
  + 0.25 * clip(min(in_degree, out_degree) / 5)
  + 0.25 * coalesce(rapid_outflow_ratio, 0.5)

terminal =
    0.45 * in_degree_percentile
  + 0.35 * in_kzt_percentile
  + 0.20 * 1.0

peripheral = max(0.25, 1 - max(other_candidate_scores))
```

Score выбранной роли округляется до шести знаков в storage и CSV.

## 6. Priority score

```text
base_priority =
    0.30 * role_score
  + 0.25 * seed_reach_percentile
  + 0.20 * pagerank_percentile
  + 0.15 * betweenness_percentile
  + 0.10 * max(in_kzt, out_kzt)_percentile

priority_score = base_priority * (0.90 if truncated_by_depth else 1.00)
```

Scores ограничиваются диапазоном `[0, 1]`, округляются до шести знаков и сортируются по:

1. priority score по убыванию;
2. role score по убыванию;
3. GID по возрастанию как десятичное целое число.

Коэффициент границы depth снижает ложную уверенность, но не исключает неопределённые узлы из проверки аналитиком.

## 7. Шаблоны evidence

Evidence создаётся из фиксированных шаблонов и ограничивается 200 символами. Примеры:

- `consolidator`: `Received from {in_degree} payers: {in_kzt} KZT; sent onward {pass_pct}%. {seed_reach_count} seeds upstream.`
- `distributor`: `Sent {out_kzt} KZT to {out_degree} recipients; observed {in_degree} payers. Fan-out indicator.`
- `transit`: `Observed in/out: {in_kzt}/{out_kzt} KZT; pass-through {ratio}; rapid outflow {rapid_pct}%.`
- `terminal`: `Received {in_kzt} KZT from {in_degree} payers; no visible outflow before depth boundary.`
- `coordinator`: `Links paths from {seed_reach_count} seeds; in/out degree {in_degree}/{out_degree}; bridge percentile {betweenness_pct}.`
- граничный `peripheral`: `Depth-4 boundary: outgoing activity is unobserved; role evidence is insufficient.`

Формулировки используют слова `observed`, `indicator`, `candidate` и `for review` и никогда не утверждают виновность.

## 8. Гипотезы кластеров

Demo mode выбирает детерминированный шаблон по агрегатам кластера:

- минимум два seed и внутренний оборот не ниже 75-го percentile среди кластеров: `Multi-seed connected transfer community for analyst review`;
- роль узла с наивысшим priority — `distributor`: `Community organized around a distribution pattern`;
- роль узла с наивысшим priority — `consolidator`: `Community with observed consolidation indicators`;
- минимум 50% узлов имеют роль `terminal`: `Recipient-heavy community with limited visible onward flow`;
- иначе: `Transfer community without a dominant structural pattern`.

Live mode может переформулировать выбранный шаблон через Structured Outputs, но не может добавлять факты или атрибуты.

## 9. Обязательные тесты

- Граничный узел без outflow не получает роль terminal.
- Seed pass-through не влияет на доступность роли для seed.
- Каждый входной узел получает ровно одну роль и один кластер.
- Повторный запуск создаёт побайтово стабильные упорядоченные строки CSV после нормализованного форматирования.
- Каждая строка evidence непустая, числовая, осторожная и не длиннее 200 символов.
- При равных значениях priority ordering остаётся детерминированным.
