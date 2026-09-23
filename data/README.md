# Dataset: four-hop intrabank transfer graph, July 2026

The dataset is an anonymized directed graph collected from 81 seed clients using outgoing transfers only.

## Collection parameters

| Parameter | Value |
|---|---|
| Seed clients | 81 GIDs |
| Period | 2026-07-01 through 2026-07-31 |
| Traversal depth | Four hops |
| Direction | Outgoing transfers only |
| Amount threshold | At least 5,000 KZT |

## Size

| Depth | New nodes |
|---|---:|
| 0, seed | 81 |
| 1 | 472 |
| 2 | 462 |
| 3 | 789 |
| 4 | 444 |
| **Total** | **2,248** |

The graph contains 3,119 unique payer-to-recipient pairs and 4,840 individual transactions.

SHA-256 fingerprints for the committed parquet files are recorded in `SHA256SUMS`.

## Files

### `edges.parquet`

One row per aggregated payer-to-recipient pair.

| Column | Type | Meaning |
|---|---|---|
| `src` | int64 | Payer GID |
| `dst` | int64 | Recipient GID |
| `sum_kzt` | float64 | Total transferred during July 2026 |
| `n_tx` | int64 | Number of individual transactions |
| `depth` | int8 | Hop where the edge was discovered, 1 through 4 |

### `nodes.parquet`

One row per unique GID, including seed clients.

| Column | Type | Meaning |
|---|---|---|
| `gid` | int64 | Synthetic client identifier |
| `depth` | int | Minimum observed hop, with 0 for seed |
| `is_seed` | bool | Whether the node is one of the 81 starting clients |

### `transactions.parquet`

One row per individual transaction for the discovered edges.

| Column | Type | Meaning |
|---|---|---|
| `src` | int64 | Payer GID |
| `dst` | int64 | Recipient GID |
| `date` | date | Transaction date |
| `sum_kzt` | float64 | Transaction amount |

## Known limitations

- The graph stops at depth 4. All 444 depth-4 nodes have no visible outgoing transfers because traversal ended, not necessarily because funds stopped there.
- Only outgoing expansion is available. Full account balances and transfers from outside the sample are unknown.
- Seed inflows are incomplete; pass-through ratios for seed accounts are biased.
- Transfers below 5,000 KZT are absent.
- Nineteen seed clients have no edges and another twelve appear only as recipients.
- There are no customer attributes and no ground-truth role labels.
- Conclusions must be phrased as review hypotheses, not declarations of guilt.

The data is anonymized and supplied for the HackAlem AI hackathon case.
