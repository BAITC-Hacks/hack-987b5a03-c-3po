import type {
  ArtifactName,
  ClusterSummary,
  DatasetImportResult,
  Gid,
  NodeDetail,
  NodeSummary,
  Page,
  ReviewCase,
  RunEvent,
  RunRecord,
} from "./types";

export const BUNDLED_DATASET_ID = "bundled";
const GID_PATTERN = /^(0|[1-9][0-9]{0,18})$/;
const MAX_GID = "9223372036854775807";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function validGid(value: string): value is Gid {
  return (
    GID_PATTERN.test(value) &&
    (value.length < MAX_GID.length || value <= MAX_GID)
  );
}

function requireGid(value: unknown): asserts value is Gid {
  if (typeof value !== "string" || !validGid(value)) {
    throw new Error("The API returned a GID that is not a decimal string.");
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "The backend is unavailable. Start the Phase 4 API and retry.",
      0,
    );
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new ApiError(
      "The backend did not return JSON for this request.",
      response.status,
    );
  }
  const body = (await response.json()) as T & {
    detail?: string;
    error?: { code?: string; message?: string };
  };
  if (!response.ok) {
    const message =
      typeof body?.error?.message === "string"
        ? body.error.message
        : typeof body?.detail === "string"
          ? body.detail
          : `Request failed (${response.status}).`;
    throw new ApiError(message, response.status, body?.error?.code);
  }
  return body;
}

export async function getHealth(): Promise<{
  status: string;
  mode: string;
  backend_ready: boolean;
  live_configured: boolean;
}> {
  return request("/health");
}

export async function createRun(
  mode: RunRecord["mode"],
  datasetId = BUNDLED_DATASET_ID,
): Promise<RunRecord> {
  return request("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset_id: datasetId, mode }),
  });
}

export async function importDataset(
  files: File[],
  seedGids: Gid[],
): Promise<DatasetImportResult> {
  const uploaded = await Promise.all(
    files.map(async (file) => ({ name: file.name, content: await file.text() })),
  );
  return request("/api/datasets/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files: uploaded, seed_gids: seedGids }),
  });
}

export function createDemoRun(): Promise<RunRecord> {
  return createRun("demo");
}

export async function executeRun(runId: string): Promise<void> {
  await request(`/api/runs/${encodeURIComponent(runId)}/execute`, {
    method: "POST",
  });
}

export function getRun(runId: string): Promise<RunRecord> {
  return request(`/api/runs/${encodeURIComponent(runId)}`);
}

export async function getTopNodes(runId: string): Promise<Page<NodeSummary>> {
  const page = await request<Page<NodeSummary>>(
    `/api/runs/${encodeURIComponent(runId)}/nodes?limit=20&offset=0`,
  );
  page.items.forEach((item) => requireGid(item.gid));
  return page;
}

export async function getNode(
  runId: string,
  gid: Gid,
  radius: 1 | 2,
): Promise<NodeDetail> {
  requireGid(gid);
  const detail = await request<{
    node: Omit<NodeDetail, "ego_graph">;
    ego_graph: NodeDetail["ego_graph"];
  }>(
    `/api/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(gid)}?neighbor_hops=${radius}&max_nodes=100&max_edges=200`,
  );
  requireGid(detail.node.gid);
  requireGid(detail.ego_graph.center_gid);
  detail.ego_graph.nodes.forEach((item) => requireGid(item.gid));
  detail.ego_graph.edges.forEach((edge) => {
    requireGid(edge.src);
    requireGid(edge.dst);
  });
  return { ...detail.node, ego_graph: detail.ego_graph };
}

export async function getClusters(
  runId: string,
): Promise<Page<ClusterSummary>> {
  const page = await request<Page<ClusterSummary>>(
    `/api/runs/${encodeURIComponent(runId)}/clusters?limit=100&offset=0`,
  );
  page.items.forEach((cluster) => cluster.top_gids.forEach(requireGid));
  return page;
}

export async function getCase(caseId: string): Promise<ReviewCase> {
  const reviewCase = await request<ReviewCase>(
    `/api/cases/${encodeURIComponent(caseId)}`,
  );
  reviewCase.target_gids.forEach(requireGid);
  return reviewCase;
}

export async function resetDemo(): Promise<void> {
  await request("/api/demo/reset", { method: "POST" });
}

export function artifactUrl(runId: string, name: ArtifactName): string {
  return `/api/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`;
}

export async function getEventHistory(runId: string): Promise<RunEvent[]> {
  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/events?follow=false`,
    {
      headers: { Accept: "text/event-stream" },
    },
  );
  if (!response.ok)
    throw new ApiError(
      "The execution trace could not be loaded.",
      response.status,
    );
  const stream = await response.text();
  return stream
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data: "))
    .flatMap((line) => {
      try {
        const event = JSON.parse(line.slice(6)) as RunEvent;
        return typeof event.event_id === "string" &&
          Number.isInteger(event.sequence) &&
          typeof event.summary === "string"
          ? [event]
          : [];
      } catch {
        return [];
      }
    });
}

export function subscribeToEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onDisconnect: () => void,
): () => void {
  const source = new EventSource(
    `/api/runs/${encodeURIComponent(runId)}/events`,
  );
  const receive = (message: MessageEvent<string>) => {
    try {
      const event = JSON.parse(message.data) as RunEvent;
      if (
        typeof event.event_id === "string" &&
        Number.isInteger(event.sequence) &&
        typeof event.summary === "string"
      ) {
        onEvent(event);
        if (event.kind === "completed" || event.kind === "failed")
          source.close();
      }
    } catch {
      // Ignore a malformed event; run status still comes from the status endpoint.
    }
  };
  source.onmessage = receive;
  for (const kind of [
    "started",
    "tool_started",
    "tool_completed",
    "decision",
    "action",
    "warning",
    "verification",
    "failed",
    "completed",
  ]) {
    source.addEventListener(kind, receive as EventListener);
  }
  source.onerror = onDisconnect;
  return () => source.close();
}
