import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./GraphView", () => ({
  default: () => <div data-testid="ego-graph" />,
}));

const runId = "b8c877b7-939d-4fda-bc8c-e85fcad0a3ee";
const caseId = "d3712e8a-ceab-4d0b-a71f-8a46a5ed2d5b";
const gid = "9007199254740993";

const created = {
  run_id: runId,
  dataset_id: "bundled",
  mode: "demo",
  status: "created",
  executing: false,
  verification_status: "pending",
  verification: {},
  counts: {},
  warnings: [],
  case_id: null,
  created_at: "2026-09-23T08:00:00Z",
  result: null,
};
const completed = {
  ...created,
  status: "completed",
  verification_status: "passed",
  case_id: caseId,
  counts: {
    n_nodes: 2248,
    assigned_count: 2248,
    n_clusters: 91,
    ranked_count: 20,
  },
  warnings: [
    "Depth-4 nodes may be truncated by the four-hop collection boundary.",
  ],
  result: {
    status: "completed",
    summary: "Review case created and verified.",
    recommended_next_step: "Analyst reviews targets.",
  },
};
const node = {
  gid,
  role: "transit",
  role_score: 0.83,
  priority_score: 0.91,
  cluster_id: 7,
  evidence: "2 incoming counterparties, 1 outgoing counterparty.",
  uncertainty_flags: ["date_only_order_unknown"],
  depth: 2,
  is_seed: false,
  in_degree: 2,
  out_degree: 1,
  in_kzt: 120000,
  out_kzt: 95000,
  truncated_by_depth: false,
};
const cluster = {
  cluster_id: 7,
  n_nodes: 12,
  n_seed: 2,
  sum_kzt_internal: 800000,
  top_gids: [gid],
  hypothesis: "Connected transfer community for analyst review.",
};

class MockEventSource {
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener() {}
  close() {}
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("bundled demo workflow", () => {
  it("creates a real API run, shows verified evidence, and resets server state", async () => {
    const requests: { path: string; method: string; body?: string }[] = [];
    const fetchMock = vi.fn(
      async (
        input: RequestInfo | URL,
        init?: RequestInit,
      ): Promise<Response> => {
        const path = String(input);
        const method = init?.method ?? "GET";
        requests.push({ path, method, body: init?.body?.toString() });
        if (path === "/health")
          return json({
            status: "ok",
            mode: "demo",
            backend_ready: true,
            live_configured: false,
          });
        if (path === "/api/runs" && method === "POST")
          return json(created, 201);
        if (path === `/api/runs/${runId}/execute` && method === "POST")
          return json(
            {
              run_id: runId,
              status: "created",
              executing: true,
              started: true,
            },
            202,
          );
        if (path === `/api/runs/${runId}`) return json(completed);
        if (path === `/api/runs/${runId}/events?follow=false`)
          return new Response(
            `id: 1\nevent: completed\ndata: ${JSON.stringify({ event_id: "event-1", sequence: 1, kind: "completed", tool_name: null, summary: "Run completed after passed verification", created_at: "2026-09-23T08:00:05Z" })}\n\n`,
            { headers: { "content-type": "text/event-stream" } },
          );
        if (path === `/api/runs/${runId}/nodes?limit=20&offset=0`)
          return json({ items: [node], total: 2248, limit: 20, offset: 0 });
        if (path === `/api/runs/${runId}/clusters?limit=100&offset=0`)
          return json({ items: [cluster], total: 91, limit: 100, offset: 0 });
        if (path === `/api/cases/${caseId}`)
          return json({
            case_id: caseId,
            run_id: runId,
            title: "Priority structural indicators for analyst review",
            status: "ready_for_review",
            target_gids: [gid],
            created_at: "2026-09-23T08:00:05Z",
          });
        if (path.startsWith(`/api/runs/${runId}/nodes/${gid}?neighbor_hops=`))
          return json({
            node,
            cluster,
            ego_graph: {
              center_gid: gid,
              neighbor_hops: path.includes("neighbor_hops=2") ? 2 : 1,
              directed: true,
              nodes: [{ gid, role: "transit", is_seed: false }],
              edges: [],
              truncated: false,
            },
          });
        if (path === "/api/demo/reset" && method === "POST")
          return json({ deleted_runs: 1 });
        throw new Error(`Unexpected request: ${method} ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", MockEventSource);

    render(<App />);
    const start = await screen.findByRole("button", {
      name: /Start bundled demo/i,
    });
    fireEvent.click(start);

    await screen.findByText("Verified run complete");
    await screen.findByText(gid);
    expect(
      requests.some(
        (request) =>
          request.path === "/api/runs" &&
          request.body === '{"dataset_id":"bundled","mode":"demo"}',
      ),
    ).toBe(true);
    expect(
      requests.some((request) => request.path === `/api/runs/${runId}/execute`),
    ).toBe(true);
    expect(screen.getByText("Verified")).toBeTruthy();
    expect(
      screen.getByText("Run completed after passed verification"),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Depth-4 nodes may be truncated by the four-hop collection boundary.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText("Priority structural indicators for analyst review"),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: /Node assessments/i })
        .getAttribute("href"),
    ).toBe(`/api/runs/${runId}/artifacts/nodes_roles.csv`);

    fireEvent.change(
      screen.getByRole("combobox", { name: "Select language" }),
      {
        target: { value: "ru" },
      },
    );
    expect(screen.getByText("Запуск завершён после проверки")).toBeTruthy();
    expect(
      screen.getByText(
        "Узлы на глубине 4 могут быть обрезаны границей сбора данных.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByRole("heading", {
        name: "Приоритетные структурные признаки для проверки аналитиком",
      }),
    ).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox", { name: "Выбрать язык" }), {
      target: { value: "en" },
    });

    fireEvent.click(screen.getByRole("button", { name: `View client ${gid}` }));
    await screen.findByRole("dialog", { name: `Client ${gid} detail` });
    await screen.findByTestId("ego-graph");
    fireEvent.click(screen.getByRole("button", { name: "2 hops" }));
    await waitFor(() =>
      expect(
        requests.some((request) =>
          request.path.includes(`nodes/${gid}?neighbor_hops=2`),
        ),
      ).toBe(true),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Close client detail" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reset demo" }));
    await waitFor(() =>
      expect(
        requests.some((request) => request.path === "/api/demo/reset"),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(sessionStorage.getItem("aml-agent-demo-run-id")).toBeNull(),
    );
  });

  it("waits for independent verification before loading a case", async () => {
    const requests: string[] = [];
    let phase: "case_created" | "completed" = "case_created";
    sessionStorage.setItem("aml-agent-demo-run-id", runId);
    vi.stubGlobal("EventSource", MockEventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        requests.push(path);
        if (path === "/health")
          return json({
            status: "ok",
            mode: "demo",
            backend_ready: true,
            live_configured: false,
          });
        if (path === `/api/runs/${runId}`)
          return json(
            phase === "completed"
              ? completed
              : {
                  ...created,
                  case_id: caseId,
                  status: "case_created",
                  executing: true,
                },
          );
        if (path === `/api/runs/${runId}/events?follow=false`)
          return new Response("", {
            headers: { "content-type": "text/event-stream" },
          });
        if (path === `/api/runs/${runId}/nodes?limit=20&offset=0`)
          return json({ items: [], total: 0, limit: 20, offset: 0 });
        if (path === `/api/runs/${runId}/clusters?limit=100&offset=0`)
          return json({ items: [], total: 0, limit: 100, offset: 0 });
        if (path === `/api/cases/${caseId}`)
          return json({
            case_id: caseId,
            run_id: runId,
            title: "Priority structural indicators for analyst review",
            status: "ready_for_review",
            target_gids: [gid],
            created_at: "2026-09-23T08:00:05Z",
          });
        throw new Error(`Unexpected request: ${path}`);
      }),
    );

    render(<App />);
    await screen.findByText("Analysis in progress");
    expect(requests).not.toContain(`/api/cases/${caseId}`);
    expect(screen.queryByRole("alert")).toBeNull();

    phase = "completed";
    await waitFor(() => expect(requests).toContain(`/api/cases/${caseId}`), {
      timeout: 3000,
    });
    await screen.findByText(
      "Priority structural indicators for analyst review",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("moves the active navigation state and switches all three languages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({
          status: "ok",
          mode: "demo",
          backend_ready: true,
          live_configured: false,
        }),
      ),
    );
    const view = render(<App />);
    const sidebar = screen.getAllByRole("navigation", {
      name: "Workspace sections",
    })[0];
    const workflow = within(sidebar).getByRole("link", {
      name: "Run workflow",
    });
    fireEvent.click(workflow);
    expect(workflow.getAttribute("aria-current")).toBe("location");
    expect(
      within(sidebar)
        .getByRole("link", { name: "Overview" })
        .getAttribute("aria-current"),
    ).toBeNull();

    fireEvent.change(
      screen.getByRole("combobox", { name: "Select language" }),
      { target: { value: "ru" } },
    );
    expect(
      screen.getByRole("heading", { name: "Запуск анализа" }),
    ).toBeTruthy();
    expect(window.localStorage.getItem("aml-agent-language")).toBe("ru");
    fireEvent.change(screen.getByRole("combobox", { name: "Выбрать язык" }), {
      target: { value: "kk" },
    });
    expect(
      screen.getByRole("heading", { name: "Талдауды іске қосу" }),
    ).toBeTruthy();
    expect(document.documentElement.lang).toBe("kk");
    view.unmount();
    render(<App />);
    expect(
      screen.getByRole("heading", { name: "Талдауды іске қосу" }),
    ).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox", { name: "Тілді таңдау" }), {
      target: { value: "en" },
    });
    expect(screen.getByRole("heading", { name: "Run workflow" })).toBeTruthy();
    expect(screen.queryByText("API connected")).toBeNull();
  });
});
