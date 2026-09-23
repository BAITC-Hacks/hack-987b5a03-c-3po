import { describe, expect, it, vi } from "vitest";
import { createRun, getNode, validGid } from "./api";

describe("Phase 4 client boundary", () => {
  it("keeps large GIDs as canonical int64 decimal strings", () => {
    expect(validGid("9007199254740993")).toBe(true);
    expect(validGid("9223372036854775807")).toBe(true);
    expect(validGid("9223372036854775808")).toBe(false);
    expect(validGid("01")).toBe(false);
    expect(validGid("1e16")).toBe(false);
  });

  it("maps the nested node response and requests a bounded directed graph", async () => {
    const gid = "9007199254740993";
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(
          JSON.stringify({
            node: {
              gid,
              role: "transit",
              evidence: "2 incoming, 1 outgoing",
              uncertainty_flags: [],
            },
            ego_graph: {
              center_gid: gid,
              neighbor_hops: 2,
              directed: true,
              truncated: false,
              nodes: [{ gid, role: "transit", is_seed: false }],
              edges: [],
            },
          }),
          { headers: { "content-type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const detail = await getNode(
      "b8c877b7-939d-4fda-bc8c-e85fcad0a3ee",
      gid,
      2,
    );
    expect(detail.gid).toBe(gid);
    expect(detail.ego_graph.center_gid).toBe(gid);
    expect(fetchMock.mock.calls[0][0]).toContain(
      `nodes/${gid}?neighbor_hops=2&max_nodes=100&max_edges=200`,
    );
  });

  it("shows the API error message without echoing invalid input", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              error: {
                code: "NODE_NOT_FOUND",
                message: "Node not found in this run.",
              },
            }),
            { status: 404, headers: { "content-type": "application/json" } },
          ),
      ),
    );

    await expect(
      getNode("b8c877b7-939d-4fda-bc8c-e85fcad0a3ee", "9007199254740993", 1),
    ).rejects.toMatchObject({
      status: 404,
      code: "NODE_NOT_FOUND",
      message: "Node not found in this run.",
    });
  });

  it("sends the explicitly selected provider mode when creating a run", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            run_id: "b8c877b7-939d-4fda-bc8c-e85fcad0a3ee",
            dataset_id: "bundled",
            mode: "live",
            status: "created",
            executing: false,
            verification_status: "pending",
            verification: {},
            counts: {},
            warnings: [],
            case_id: null,
            created_at: "2026-09-23T00:00:00Z",
            result: null,
          }),
          { status: 201, headers: { "content-type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createRun("live");

    expect(fetchMock).toHaveBeenCalledOnce();
    const init = fetchMock.mock.calls[0][1];
    expect(JSON.parse(String(init?.body))).toEqual({
      dataset_id: "bundled",
      mode: "live",
    });
  });
});
