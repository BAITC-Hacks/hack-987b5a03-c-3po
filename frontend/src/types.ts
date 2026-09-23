// GIDs are decimal strings throughout the browser. Never parse them as numbers.
export type Gid = string;

export type RunStatus =
  | "created"
  | "validated"
  | "graph_ready"
  | "analyzed"
  | "clustered"
  | "classified"
  | "ranked"
  | "case_created"
  | "exported"
  | "verified"
  | "completed"
  | "verification_failed"
  | "failed";

export type VerificationStatus = "pending" | "passed" | "failed";

export interface RunCounts {
  n_nodes?: number;
  n_edges?: number;
  n_transactions?: number;
  n_seed?: number;
  assigned_count?: number;
  n_clusters?: number;
  ranked_count?: number;
}

export interface RunRecord {
  run_id: string;
  dataset_id: string;
  mode: "demo" | "live";
  status: RunStatus;
  executing: boolean;
  verification_status: VerificationStatus;
  verification: Record<string, boolean>;
  counts: RunCounts;
  warnings: string[];
  case_id: string | null;
  created_at: string;
  result: {
    status: "completed" | "failed" | "needs_user_action";
    summary: string;
    recommended_next_step: string;
  } | null;
}

export interface DatasetImportResult {
  dataset_id: string;
  n_files: number;
  n_transactions: number;
  n_nodes: number;
  n_edges: number;
  n_seed: number;
  period_start: string;
  period_end: string;
  warnings: string[];
}

export type EventKind =
  | "started"
  | "tool_started"
  | "tool_completed"
  | "decision"
  | "action"
  | "warning"
  | "verification"
  | "failed"
  | "completed";

export interface RunEvent {
  event_id: string;
  sequence: number;
  kind: EventKind;
  tool_name: string | null;
  summary: string;
  created_at: string;
}

export interface NodeSummary {
  gid: Gid;
  role: string;
  role_score: number;
  priority_score: number;
  cluster_id: number;
  evidence: string;
  uncertainty_flags: string[];
  rank?: number;
}

export interface NodeDetail extends NodeSummary {
  depth: number;
  is_seed: boolean;
  in_degree: number;
  out_degree: number;
  in_kzt: number;
  out_kzt: number;
  truncated_by_depth: boolean;
  ego_graph: EgoGraph;
}

export interface EgoNode {
  gid: Gid;
  role: string;
  is_seed: boolean;
}

export interface EgoEdge {
  src: Gid;
  dst: Gid;
  sum_kzt: number;
  n_tx: number;
}

export interface EgoGraph {
  center_gid: Gid;
  neighbor_hops: number;
  directed: true;
  nodes: EgoNode[];
  edges: EgoEdge[];
  truncated: boolean;
}

export interface ClusterSummary {
  cluster_id: number;
  n_nodes: number;
  n_seed: number;
  sum_kzt_internal: number;
  top_gids: Gid[];
  hypothesis: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReviewCase {
  case_id: string;
  run_id: string;
  title: string;
  status: "ready_for_review" | "in_review" | "closed";
  target_gids: Gid[];
  created_at: string;
}

export type ArtifactName =
  | "nodes_roles.csv"
  | "clusters.csv"
  | "top_nodes.csv"
  | "aml_review_report.xlsx"
  | "audit.json";
