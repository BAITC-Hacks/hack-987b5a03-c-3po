import { describe, expect, it, vi } from "vitest";
import { getNode, validGid } from "./api";

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
});
