# AML Agent analytical ruleset v1

## 1. Principle

All authoritative roles, scores, clusters, and rankings are calculated deterministically. The model may summarize persisted evidence but cannot generate analytical values.

## 2. Structural features

- `in_degree`, `out_degree`: number of distinct observed counterparties.
- `in_kzt`, `out_kzt`: observed volume within the sampled graph.
- `in_tx`, `out_tx`: observed transaction counts.
- `pass_through_ratio = out_kzt / in_kzt` when observed inflow is positive and the node is not a seed.
- `pagerank`: directed PageRank weighted by `sum_kzt`.
- `betweenness`: directed unweighted approximate betweenness with deterministic sample seed 42. Amount is not used as a distance.
- `seed_reach_count`: number of distinct seeds from which the node is reachable.
- `rapid_outflow_ratio`: share of outflow occurring zero to two calendar days after any observed inflow.
- `truncated_by_depth`: depth 4 and no visible outgoing edge.

Percentile ranks are calculated across all nodes with tie method `average`.

## 3. Community detection

Louvain is applied to an undirected projection whose reciprocal edge amounts are summed. This projection is used only for communities. Direction remains authoritative for roles and evidence.

MVP parameters:

- resolution: `1.0`;
- random seed: `42`;
- isolated nodes receive singleton clusters;
- every node receives exactly one `cluster_id`.

## 4. Role eligibility and precedence

Rules are evaluated in this order so every node receives one primary role.

### coordinator

Eligible when all are true:

- not a seed;
- depth is less than 4;
- has both incoming and outgoing edges;
- `seed_reach_count` is at or above the 90th percentile;
- `coordinator_index` is at or above the 99th percentile.

```text
coordinator_index =
    0.30 * betweenness_percentile
  + 0.25 * seed_reach_percentile
  + 0.20 * pagerank_percentile
  + 0.15 * in_degree_percentile
  + 0.10 * out_degree_percentile
```

### consolidator

Eligible when:

- not a seed;
- `in_degree >= 5`;
- `pass_through_ratio < 0.5`.

### distributor

Eligible when:

- `out_degree >= 10`;
- `out_degree >= 2 * max(in_degree, 1)`.

This rule does not use seed pass-through ratios.

### transit

Eligible when:

- not a seed;
- depth is less than 4;
- `in_degree >= 2` and `out_degree >= 1`;
- `0.8 <= pass_through_ratio <= 1.2`.

`rapid_outflow_ratio` increases confidence but is not required because only calendar dates are available.

### terminal

Eligible when:

- not a seed;
- depth is less than 4;
- `out_degree == 0`;
- `in_degree >= 2`.

A depth-4 node can never qualify only because outgoing transfers are absent.

### peripheral

Fallback for nodes without sufficient evidence for another role, including isolated seeds and boundary-limited nodes with no stronger observed behavior.

## 5. Role score

Every component below is clipped to `[0, 1]`.

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

The selected role score is rounded to six decimals in storage and CSV.

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

Scores are clipped to `[0, 1]`, rounded to six decimals, then sorted by:

1. priority score descending;
2. role score descending;
3. GID ascending as a decimal integer.

The depth-boundary factor reduces false certainty but does not remove uncertain nodes from analyst review.

## 7. Evidence templates

Evidence is generated from fixed templates and limited to 200 characters. Examples:

- `consolidator`: `Received from {in_degree} payers: {in_kzt} KZT; sent onward {pass_pct}%. {seed_reach_count} seeds upstream.`
- `distributor`: `Sent {out_kzt} KZT to {out_degree} recipients; observed {in_degree} payers. Fan-out indicator.`
- `transit`: `Observed in/out: {in_kzt}/{out_kzt} KZT; pass-through {ratio}; rapid outflow {rapid_pct}%.`
- `terminal`: `Received {in_kzt} KZT from {in_degree} payers; no visible outflow before depth boundary.`
- `coordinator`: `Links paths from {seed_reach_count} seeds; in/out degree {in_degree}/{out_degree}; bridge percentile {betweenness_pct}.`
- boundary `peripheral`: `Depth-4 boundary: outgoing activity is unobserved; role evidence is insufficient.`

The wording uses `observed`, `indicator`, `candidate`, and `for review`; it never states guilt.

## 8. Cluster hypotheses

Demo mode selects a deterministic template from cluster aggregates:

- multiple seeds plus high internal turnover: `Multi-seed connected transfer community for analyst review`;
- dominant fan-out node: `Community organized around a distribution pattern`;
- dominant retained inflow: `Community with observed consolidation indicators`;
- terminal-heavy: `Recipient-heavy community with limited visible onward flow`;
- otherwise: `Transfer community without a dominant structural pattern`.

Live mode may rephrase the chosen template through Structured Outputs but cannot add facts or attributes.

## 9. Required tests

- Boundary node with no outflow is not labeled terminal.
- Seed pass-through does not affect seed role eligibility.
- Every input node receives exactly one role and cluster.
- Repeated execution produces byte-stable ordered CSV rows after normalized formatting.
- Each evidence string is non-empty, numeric, cautious, and at most 200 characters.
- Priority ordering is deterministic for ties.
