import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import type { EgoGraph, Gid } from "./types";

interface Props {
  graph: EgoGraph;
  focusGid: Gid;
  onSelect: (gid: Gid) => void;
}

const roleColors: Record<string, string> = {
  coordinator: "#5f6df5",
  consolidator: "#16a5a3",
  distributor: "#e89652",
  transit: "#58a2dc",
  terminal: "#b48fd9",
  peripheral: "#8b9aac",
};

export default function GraphView({ graph, focusGid, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;
    const visible = new Set(graph.nodes.map((node) => node.gid));
    const elements: cytoscape.ElementDefinition[] = [
      ...graph.nodes.map((node) => ({
        data: {
          id: node.gid,
          label:
            node.gid === focusGid
              ? `Selected · …${node.gid.slice(-6)}`
              : `…${node.gid.slice(-6)}`,
          color: roleColors[node.role] ?? "#8b9aac",
        },
        classes: node.gid === focusGid ? "focus" : node.is_seed ? "seed" : "",
      })),
      ...graph.edges
        .filter((edge) => visible.has(edge.src) && visible.has(edge.dst))
        .map((edge, index) => ({
          data: { id: `edge-${index}`, source: edge.src, target: edge.dst },
        })),
    ];
    const instance = cytoscape({
      container: container.current,
      elements,
      layout: {
        name: "breadthfirst",
        directed: true,
        roots: [focusGid],
        padding: 24,
        spacingFactor: 1.25,
      },
      minZoom: 0.25,
      maxZoom: 2.5,
      wheelSensitivity: 0.18,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            label: "data(label)",
            color: "#23344b",
            "font-size": 10,
            "font-weight": 600,
            "text-valign": "bottom",
            "text-margin-y": 8,
            width: 22,
            height: 22,
            "border-width": 3,
            "border-color": "#fff",
          },
        },
        {
          selector: "node.focus",
          style: {
            width: 32,
            height: 32,
            "border-width": 4,
            "border-color": "#0b1727",
          },
        },
        { selector: "node.seed", style: { "border-color": "#eac577" } },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#bac8d8",
            "target-arrow-color": "#7c8da3",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.85,
            "curve-style": "bezier",
          },
        },
      ],
    });
    instance.on("tap", "node", (event) => onSelect(event.target.id()));
    return () => instance.destroy();
  }, [graph, focusGid, onSelect]);

  return (
    <div
      className="graph-canvas"
      ref={container}
      role="img"
      aria-label="Directed transfer network around the selected client"
    />
  );
}
