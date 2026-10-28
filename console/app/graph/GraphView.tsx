"use client";
// The memory graph: threads as hubs, facts around them, files at the rim, people as small nodes.
// A d3-force layout drawn in SVG, with pan, wheel zoom and pinch. Which node is selected, and
// whether the view shows open work or everything, live in the URL, so the server renders the panel.
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import Link from "next/link";
import { forceCollide, forceLink, forceManyBody, forceRadial, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import { Maximize2, Minus, Plus, Sparkles, Undo2 } from "lucide-react";
import { cx } from "@/components/ui";
import { personNodeId, threadNodeId } from "@/lib/graph";
import { initials, personName } from "@/lib/trust";
import type { EdgeKind, GraphFile, MemoryGraph, MemoryItem, RelatedThread } from "@/lib/types";
import { graphHref, type GraphParams } from "./params";

// ---------------------------------------------------------------- what is shown

type ViewEdgeKind = EdgeKind | "said_by";
interface ViewEdge { from: string; to: string; kind: ViewEdgeKind; current?: boolean | null }

/** The API's node, narrowed by type for drawing. A file is drawn changed when any fact's anchor to it is stale. */
type GraphNode =
  | { id: string; type: "fact"; label: string; grew: boolean; memory: MemoryItem; saidBy: string[] }
  | { id: string; type: "thread"; label: string; grew: boolean; thread: RelatedThread }
  | { id: string; type: "file"; label: string; grew: boolean; file: GraphFile & { current: boolean } }
  | { id: string; type: "person"; label: string; grew: boolean; identity: string };
interface ViewGraph { nodes: GraphNode[]; edges: ViewEdge[]; truncated: boolean }

function toViewGraph(g: MemoryGraph): ViewGraph {
  const changed = new Set(g.edges.filter((e) => e.kind === "anchored" && e.current === false).map((e) => e.to));
  const nodes: GraphNode[] = [];
  for (const n of g.nodes) {
    const base = { id: n.id, label: n.label, grew: n.grew };
    if (n.type === "fact" && n.memory) nodes.push({ ...base, type: "fact", memory: n.memory, saidBy: n.saidBy ?? [] });
    else if (n.type === "thread" && n.thread) nodes.push({ ...base, type: "thread", thread: n.thread });
    else if (n.type === "file" && n.file) nodes.push({ ...base, type: "file", file: { ...n.file, current: !n.file.deleted && !changed.has(n.id) } });
    else if (n.type === "person") nodes.push({ ...base, type: "person", identity: n.id.replace(/^person:/, "") || n.label });
  }
  const ids = new Set(nodes.map((n) => n.id));
  // "said by" is a field on the fact in the API, a line in the picture.
  const said: ViewEdge[] = nodes.flatMap((n) => n.type !== "fact" ? [] :
    n.saidBy.map(personNodeId).filter((p) => ids.has(p)).map((p) => ({ from: n.id, to: p, kind: "said_by" as const })));
  const edges: ViewEdge[] = [...g.edges.filter((e) => ids.has(e.from) && ids.has(e.to)), ...said];
  return { nodes, edges, truncated: g.truncated };
}

const MEMORY_EDGES: ViewEdgeKind[] = ["supersedes", "contradicts", "extends", "derived"];

/** The ids on screen: the first view is the open threads and their facts (with what those facts
 *  touch); "all" is everything; a selection narrows to that node's neighborhood; growth keeps what grew. */
function visibleIds(graph: ViewGraph, edges: ViewEdge[], p: GraphParams): Set<string> {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const neighbors = (ids: Set<string>, kinds?: ViewEdgeKind[]) => {
    const out = new Set(ids);
    for (const e of edges) {
      if (kinds && !kinds.includes(e.kind)) continue;
      if (ids.has(e.from)) out.add(e.to);
      if (ids.has(e.to)) out.add(e.from);
    }
    return out;
  };

  if (p.node && byId.has(p.node)) {
    let ids = neighbors(new Set([p.node]));
    // A fact's whole supersede chain, not only the next link.
    for (let i = 0; i < 6; i++) ids = new Set([...ids, ...neighbors(new Set([...ids].filter((id) => byId.get(id)?.type === "fact")), ["supersedes"])]);
    return ids;
  }

  let ids: Set<string>;
  const open = graph.nodes.filter((n) => n.type === "thread" && (n.thread.status === "open" || n.thread.status === "blocked"));
  if (p.view === "all" || open.length === 0) {
    ids = new Set(graph.nodes.map((n) => n.id));
  } else {
    const hubs = new Set(open.map((n) => n.id));
    const facts = new Set(graph.nodes.filter((n) => n.type === "fact" && n.memory.threadId && hubs.has(threadNodeId(n.memory.threadId))).map((n) => n.id));
    // Their facts, the facts those touch (history, contradictions, extensions), and their files and people.
    const touched = neighbors(facts, MEMORY_EDGES);
    ids = new Set([...hubs, ...neighbors(touched, ["anchored", "said_by"])]);
    for (const id of touched) ids.add(id);
  }
  if (p.grew) {
    const grown = new Set([...ids].filter((id) => byId.get(id)?.grew));
    // Keep the hubs grown facts hang from, as context.
    for (const e of edges) if (e.kind === "decided_in" && grown.has(e.from)) grown.add(e.to);
    return grown;
  }
  return ids;
}

/** Ids reachable outside the first view, for the "Everything" button. */
function hiddenCount(graph: ViewGraph, shown: Set<string>) {
  return graph.nodes.filter((n) => !shown.has(n.id)).length;
}

// ---------------------------------------------------------------- layout

interface SimNode extends SimulationNodeDatum { id: string; type: GraphNode["type"] }
type SimLink = SimulationLinkDatum<SimNode> & { kind: ViewEdgeKind };

const RADIUS: Record<GraphNode["type"], number> = { thread: 15, fact: 7, file: 6, person: 9 };
const LINK_DISTANCE: Record<ViewEdgeKind, number> = {
  decided_in: 62, supersedes: 46, contradicts: 52, extends: 44, derived: 58, anchored: 96, said_by: 104,
};

function layout(nodes: GraphNode[], edges: ViewEdge[], focus: string | null, previous: Map<string, { x: number; y: number }>) {
  const sim: SimNode[] = nodes.map((n) => {
    const prev = previous.get(n.id);
    return { id: n.id, type: n.type, ...(prev ? { x: prev.x, y: prev.y } : {}) };
  });
  const links: SimLink[] = edges.map((e) => ({ source: e.from, target: e.to, kind: e.kind }));
  const focused = Boolean(focus);
  const ring = (t: GraphNode["type"]) => (focused ? { thread: 120, fact: 110, file: 200, person: 200 }[t] : { thread: 70, fact: 190, file: 330, person: 300 }[t]);
  const simulation = forceSimulation<SimNode>(sim)
    .force("link", forceLink<SimNode, SimLink>(links).id((d) => d.id).distance((l) => LINK_DISTANCE[l.kind])
      .strength((l) => (l.kind === "decided_in" ? 0.7 : l.kind === "said_by" ? 0.08 : l.kind === "anchored" ? 0.25 : 0.5)))
    .force("charge", forceManyBody<SimNode>().strength((d) => (d.type === "thread" ? -420 : d.type === "fact" ? -110 : -70)))
    .force("collide", forceCollide<SimNode>().radius((d) => RADIUS[d.type] + (d.type === "thread" ? 22 : 9)))
    .force("radial", forceRadial<SimNode>((d) => (d.id === focus ? 0 : ring(d.type)), 0, 0)
      .strength((d) => (d.type === "file" || d.type === "person" ? 0.5 : d.type === "thread" ? 0.25 : 0.06)))
    .force("x", forceX(0).strength(0.02))
    .force("y", forceY(0).strength(0.03))
    .stop();
  const focusNode = sim.find((n) => n.id === focus);
  if (focusNode) { focusNode.fx = 0; focusNode.fy = 0; }
  simulation.tick(focused ? 220 : 320);
  const pos = new Map<string, { x: number; y: number }>();
  for (const n of sim) pos.set(n.id, { x: n.x ?? 0, y: n.y ?? 0 });
  return pos;
}

// ---------------------------------------------------------------- drawing helpers

const CATEGORY_FILL: Record<string, string> = {
  decision: "var(--info)", rule: "var(--good)", gotcha: "var(--warn)", fact: "var(--text-2)", preference: "var(--chart-2)",
};
const THREAD_RING: Record<string, string> = { open: "var(--info)", blocked: "var(--warn)", done: "var(--good)", abandoned: "var(--text-3)" };

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);
const basename = (path: string) => path.split("/").pop() ?? path;

function nodeTitle(n: GraphNode): string {
  switch (n.type) {
    case "fact": return n.memory.statement;
    case "thread": return `${n.thread.title} (${n.thread.key}) · ${n.thread.status}`;
    case "file": return `${n.file.path}${n.file.version ? ` @ ${n.file.version.slice(0, 7)}` : ""}${n.file.current ? "" : " · changed since"}`;
    case "person": return personName(n.identity);
  }
}

/** A line from a to b, trimmed by the node radii so arrows land on the rim. */
function trim(a: { x: number; y: number }, b: { x: number; y: number }, ra: number, rb: number) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  return { x1: a.x + ux * ra, y1: a.y + uy * ra, x2: b.x - ux * rb, y2: b.y - uy * rb, ux, uy, len };
}

const f = (n: number) => Math.round(n * 10) / 10;

function Edge({ e, a, b, ra, rb, dim }: { e: ViewEdge; a: { x: number; y: number }; b: { x: number; y: number }; ra: number; rb: number; dim: boolean }) {
  const arrow = e.kind === "supersedes" || e.kind === "extends" || e.kind === "derived";
  const t = trim(a, b, ra + 1, rb + (arrow ? 4 : 1));
  const common = { opacity: dim ? 0.25 : 1 };
  const line = (props: Record<string, string | number>, dx = 0, dy = 0) => (
    <line x1={f(t.x1 + dx)} y1={f(t.y1 + dy)} x2={f(t.x2 + dx)} y2={f(t.y2 + dy)} {...props} />
  );
  switch (e.kind) {
    case "contradicts": {
      // A double line: two parallels, offset either side.
      const ox = -t.uy * 2.2, oy = t.ux * 2.2;
      return <g {...common} stroke="var(--bad)" strokeWidth={1.2} data-edge="contradicts">{line({}, ox, oy)}{line({}, -ox, -oy)}</g>;
    }
    case "supersedes":
      return <g {...common} data-edge="supersedes">{line({ stroke: "var(--text-2)", strokeWidth: 1.4, markerEnd: "url(#mv-arrow)" })}</g>;
    case "extends":
      return <g {...common} data-edge="extends">{line({ stroke: "var(--accent)", strokeWidth: 1.6, strokeDasharray: "1 3.5", strokeLinecap: "round", markerEnd: "url(#mv-arrow-accent)" })}</g>;
    case "derived":
      return <g {...common} data-edge="derived">{line({ stroke: "var(--info)", strokeWidth: 1.2, strokeDasharray: "5 3", markerEnd: "url(#mv-arrow-info)" })}</g>;
    case "decided_in":
      return <g {...common} data-edge="decided_in">{line({ stroke: "var(--border-strong)", strokeWidth: 1 })}</g>;
    case "said_by":
      return <g {...common} data-edge="said_by">{line({ stroke: "var(--border-strong)", strokeWidth: 0.8, strokeDasharray: "2 3" })}</g>;
    case "anchored": {
      if (e.current !== false) return <g {...common} data-edge="anchored">{line({ stroke: "var(--text-3)", strokeWidth: 1 })}</g>;
      // The file changed: the edge is drawn broken, a gap in the middle with ticks at the break.
      const gap = Math.min(10, t.len * 0.18);
      const mx = (t.x1 + t.x2) / 2, my = (t.y1 + t.y2) / 2;
      const px = -t.uy * 4, py = t.ux * 4;
      const ax = mx - t.ux * gap, ay = my - t.uy * gap, bx = mx + t.ux * gap, by = my + t.uy * gap;
      return (
        <g {...common} stroke="var(--warn)" strokeWidth={1.3} data-edge="anchored-broken">
          <line x1={f(t.x1)} y1={f(t.y1)} x2={f(ax)} y2={f(ay)} />
          <line x1={f(bx)} y1={f(by)} x2={f(t.x2)} y2={f(t.y2)} />
          <line x1={f(ax - px)} y1={f(ay - py)} x2={f(ax + px)} y2={f(ay + py)} />
          <line x1={f(bx - px)} y1={f(by - py)} x2={f(bx + px)} y2={f(by + py)} />
        </g>
      );
    }
  }
}

function NodeShape({ n, selected }: { n: GraphNode; selected: boolean }) {
  const r = RADIUS[n.type];
  const ring = selected ? <circle r={r + 4} fill="none" stroke="var(--text)" strokeWidth={2} /> : null;
  const halo = n.grew ? <circle className="mv-grew" r={r + 7} fill="var(--accent)" opacity={0.18} /> : null;
  switch (n.type) {
    case "thread":
      return (
        <>
          {halo}
          <circle r={r} fill="var(--surface-3)" stroke={THREAD_RING[n.thread.status]} strokeWidth={2.5} />
          <circle r={4} fill={THREAD_RING[n.thread.status]} />
          {ring}
        </>
      );
    case "fact": {
      const m = n.memory;
      const fill = (m.category && CATEGORY_FILL[m.category]) || "var(--text-3)";
      const hollow = m.assertion === "claimed" || m.assertion === "inferred";
      return (
        <g opacity={m.status === "superseded" ? 0.35 : 1}>
          {halo}
          {m.status === "stale" && <circle r={r + 3} fill="none" stroke="var(--warn)" strokeWidth={1.5} strokeDasharray="2 2" />}
          <circle r={r} fill={hollow ? "var(--panel)" : fill} stroke={hollow ? (m.assertion === "claimed" ? "var(--warn)" : fill) : "var(--panel)"}
                  strokeWidth={hollow ? 1.8 : 1.5} strokeDasharray={m.assertion === "inferred" ? "2.5 2" : undefined} />
          {ring}
        </g>
      );
    }
    case "file":
      return (
        <>
          {halo}
          <rect x={-r} y={-r} width={r * 2} height={r * 2} rx={2} fill="var(--surface)" stroke={n.file.current ? "var(--text-3)" : "var(--warn)"}
                strokeWidth={1.4} strokeDasharray={n.file.current ? undefined : "3 2"} />
          {ring}
        </>
      );
    case "person":
      return (
        <>
          <circle r={r} fill="var(--surface-2)" stroke="var(--border-strong)" strokeWidth={1} />
          <text textAnchor="middle" dy="0.35em" fontSize={7.5} fontWeight={600} fill="var(--text-2)">{initials(n.identity)}</text>
          {ring}
        </>
      );
  }
}

function nodeLabel(n: GraphNode, showFacts: boolean): string | null {
  switch (n.type) {
    case "thread": return truncate(n.thread.title || n.thread.key, 24);
    case "file": return truncate(basename(n.file.path), 24);
    case "person": return truncate(personName(n.identity), 18);
    case "fact": return showFacts ? truncate(n.memory.statement, 34) : null;
  }
}

// ---------------------------------------------------------------- the view

interface Transform { x: number; y: number; k: number }
const MIN_K = 0.35, MAX_K = 4;
const clampK = (k: number) => Math.min(MAX_K, Math.max(MIN_K, k));

export function GraphView({ graph: apiGraph, params, base = "/", compact = false }: {
  graph: MemoryGraph; params: GraphParams; base?: string;
  /** A small picture without the toolbar (the paste screen): a tap opens the node on the full graph at `base`. */
  compact?: boolean;
}) {
  const router = useRouter();
  const box = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [t, setT] = useState<Transform>({ x: 0, y: 0, k: 1 });
  const previous = useRef(new Map<string, { x: number; y: number }>());

  const graph = useMemo(() => toViewGraph(apiGraph), [apiGraph]);
  const allEdges = graph.edges;
  const shown = useMemo(() => visibleIds(graph, allEdges, params), [graph, allEdges, params]);
  const nodes = useMemo(() => graph.nodes.filter((n) => shown.has(n.id)), [graph, shown]);
  const edges = useMemo(() => allEdges.filter((e) => shown.has(e.from) && shown.has(e.to)), [allEdges, shown]);
  const focus = params.node && shown.has(params.node) ? params.node : null;
  const pos = useMemo(() => {
    const p = layout(nodes, edges, focus, previous.current);
    previous.current = new Map([...previous.current, ...p]);
    return p;
  }, [nodes, edges, focus]);

  // Stretch the round layout toward the box's shape (wide on a desk, tall on a phone), then fit it
  // into the box; the user's pan and zoom sit on top.
  const bounds = (ps: Iterable<{ x: number; y: number }>) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const { x, y } of ps) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    return { x0, y0, x1, y1 };
  };
  const shaped = useMemo(() => {
    const b = bounds(pos.values());
    if (!Number.isFinite(b.x0)) return pos;
    const want = size.w / size.h, have = (b.x1 - b.x0 + 1) / (b.y1 - b.y0 + 1);
    const sx = Math.min(1.8, Math.max(0.6, Math.sqrt(want / have)));
    return new Map([...pos].map(([id, p]) => [id, { x: p.x * sx, y: p.y / sx }]));
  }, [pos, size]);
  const fit = useMemo(() => {
    const { x0, y0, x1, y1 } = bounds(shaped.values());
    if (!Number.isFinite(x0)) return { cx: 0, cy: 0, s: 1 };
    const pad = compact ? 110 : 60; // compact draws fact labels, which reach past their nodes
    const s = Math.min(size.w / (x1 - x0 + pad * 2), size.h / (y1 - y0 + pad * 2), 1.6);
    return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, s };
  }, [shaped, size, compact]);
  useEffect(() => setT({ x: 0, y: 0, k: 1 }), [pos]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: Math.max(240, entry.contentRect.width), h: Math.max(240, entry.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Zoom about a point given in box pixels.
  const zoomAt = (px: number, py: number, factor: number) =>
    setT((cur) => {
      const k = clampK(cur.k * factor);
      const ratio = k / cur.k;
      const ox = px - size.w / 2, oy = py - size.h / 2;
      return { k, x: ox - (ox - cur.x) * ratio, y: oy - (oy - cur.y) * ratio };
    });

  // Wheel zoom needs a non-passive listener to keep the page from scrolling.
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0022)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  // Pan with one pointer, pinch with two. A press that barely moves is a tap: it selects.
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ moved: number; pinch?: { d: number; k: number; mx: number; my: number; tx: number; ty: number } }>({ moved: 0 });
  const local = (e: { clientX: number; clientY: number }) => {
    const rect = svg.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };
  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    svg.current?.setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, local(e));
    if (pointers.current.size === 1) gesture.current = { moved: 0 };
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      gesture.current.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, k: t.k, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, tx: t.x, ty: t.y };
      gesture.current.moved = 99;
    }
  };
  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    const now = local(e);
    pointers.current.set(e.pointerId, now);
    const pinch = gesture.current.pinch;
    if (pinch && pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      const k = clampK(pinch.k * (Math.hypot(a.x - b.x, a.y - b.y) / pinch.d));
      const ratio = k / pinch.k;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const ox = pinch.mx - size.w / 2, oy = pinch.my - size.h / 2;
      setT({ k, x: ox - (ox - pinch.tx) * ratio + (mx - pinch.mx), y: oy - (oy - pinch.ty) * ratio + (my - pinch.my) });
      return;
    }
    const dx = now.x - prev.x, dy = now.y - prev.y;
    gesture.current.moved += Math.abs(dx) + Math.abs(dy);
    if (gesture.current.moved > 4) setT((cur) => ({ ...cur, x: cur.x + dx, y: cur.y + dy }));
  };
  const onPointerUp = (e: ReactPointerEvent<SVGSVGElement>) => {
    const wasTap = pointers.current.size === 1 && gesture.current.moved <= 6;
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) gesture.current.pinch = undefined;
    if (!wasTap) return;
    const target = document.elementFromPoint(e.clientX, e.clientY)?.closest<SVGGElement>("[data-node]");
    const id = target?.dataset.node;
    if (id) select(id);
    else if (params.node) select(null);
  };

  const select = (id: string | null) => router.push(graphHref(base, { ...params, node: id ?? undefined }), { scroll: false });

  const scale = fit.s * t.k;
  const toScreen = `translate(${f(size.w / 2 + t.x)} ${f(size.h / 2 + t.y)}) scale(${f(scale * 1000) / 1000}) translate(${f(-fit.cx)} ${f(-fit.cy)})`;
  const showFactLabels = Boolean(focus) || compact || t.k > 1.4;
  // Labels keep one size on screen, whatever the zoom.
  const ls = 1 / scale;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const neighborhood = focus ? new Set([focus, ...edges.filter((e) => e.from === focus || e.to === focus).flatMap((e) => [e.from, e.to])]) : null;
  const grewCount = graph.nodes.filter((n) => n.grew).length;
  const hidden = params.node ? 0 : hiddenCount(graph, shown);

  return (
    <div className="relative overflow-hidden rounded-xl border border-border bg-surface">
      <div className={cx("flex flex-wrap items-center gap-2 border-b border-border px-3 py-2", compact && "hidden")}>
        {focus ? (
          <Link href={graphHref(base, { ...params, node: undefined })} scroll={false}
                className="inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12.5px] font-medium text-text-2 hover:bg-surface-2 hover:text-text">
            <Undo2 className="size-3.5" aria-hidden /> Whole graph
          </Link>
        ) : (
          <div role="group" aria-label="What to show" className="inline-flex rounded-lg border border-border p-0.5">
            {([["open", "Open work"], ["all", "Everything"]] as const).map(([v, label]) => (
              <Link key={v} href={graphHref(base, { ...params, view: v === "all" ? "all" : undefined })} scroll={false}
                    aria-current={(params.view === "all") === (v === "all") ? "true" : undefined}
                    className={cx("inline-flex h-6 items-center rounded-md px-2 text-[12px] font-medium transition-colors",
                      (params.view === "all") === (v === "all") ? "bg-surface-3 text-text" : "text-text-3 hover:text-text")}>
                {label}
              </Link>
            ))}
          </div>
        )}
        <Link href={graphHref(base, { ...params, grew: params.grew ? undefined : true })} scroll={false} aria-pressed={params.grew}
              className={cx("inline-flex h-7 items-center gap-1.5 rounded-lg border px-2 text-[12px] font-medium transition-colors",
                params.grew ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-text-2 hover:text-text")}>
          <Sparkles className="size-3.5" aria-hidden /> Grew this week <span className="tabular-nums opacity-70">{grewCount}</span>
        </Link>
        <span className="ml-auto text-[12px] text-text-3 tabular-nums">
          {nodes.length} of {graph.nodes.length} nodes{hidden > 0 && params.view !== "all" && !params.grew ? ` · ${hidden} more in Everything` : ""}
          {graph.truncated ? " · capped" : ""}
        </span>
      </div>

      <div ref={box} className={cx("relative", compact ? "h-[260px] md:h-[300px]" : "h-[62vh] min-h-[360px] md:h-[560px]")}>
        <svg
          ref={svg}
          width={size.w}
          height={size.h}
          className="absolute inset-0 block size-full cursor-grab touch-none select-none active:cursor-grabbing"
          role="img"
          aria-label={`Memory graph: ${nodes.length} nodes, ${edges.length} edges`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <defs>
            {([["mv-arrow", "var(--text-2)"], ["mv-arrow-accent", "var(--accent)"], ["mv-arrow-info", "var(--info)"]] as const).map(([id, color]) => (
              <marker key={id} id={id} viewBox="0 0 8 8" refX={7} refY={4} markerWidth={7} markerHeight={7} orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 z" fill={color} />
              </marker>
            ))}
          </defs>
          <g transform={toScreen}>
            <g>
              {edges.map((e, i) => {
                const a = shaped.get(e.from), b = shaped.get(e.to), na = byId.get(e.from), nb = byId.get(e.to);
                if (!a || !b || !na || !nb) return null;
                const dim = Boolean(neighborhood) && !(neighborhood!.has(e.from) && neighborhood!.has(e.to));
                return <Edge key={`${e.kind}-${e.from}-${e.to}-${i}`} e={e} a={a} b={b} ra={RADIUS[na.type]} rb={RADIUS[nb.type]} dim={dim} />;
              })}
            </g>
            <g>
              {nodes.map((n) => {
                const p = shaped.get(n.id)!;
                const label = nodeLabel(n, showFactLabels);
                const selected = n.id === focus;
                return (
                  <g key={n.id} data-node={n.id} data-type={n.type} transform={`translate(${f(p.x)} ${f(p.y)})`}
                     role="button" tabIndex={0} aria-label={`${n.type}: ${nodeTitle(n)}`} className="cursor-pointer outline-none [&:focus-visible>circle:first-of-type]:stroke-[var(--ring)]"
                     opacity={neighborhood && !neighborhood.has(n.id) ? 0.45 : 1}
                     onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(n.id); } }}>
                    <title>{nodeTitle(n)}</title>
                    {/* A generous invisible hit area for fingers. */}
                    <circle r={RADIUS[n.type] + 10} fill="transparent" />
                    <NodeShape n={n} selected={selected} />
                    {label && (
                      <text y={RADIUS[n.type] + (n.type === "thread" ? 14 : 12) * ls} textAnchor="middle" fontSize={(n.type === "thread" ? 11.5 : 10.5) * ls}
                            fontWeight={n.type === "thread" || selected ? 600 : 400}
                            fontFamily={n.type === "file" ? "var(--font-mono)" : undefined}
                            fill={n.type === "fact" ? "var(--text)" : "var(--text-2)"} stroke="var(--surface)" strokeWidth={3 * ls} paintOrder="stroke"
                            className="pointer-events-none">
                        {label}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </g>
        </svg>

        <div className="absolute right-2 bottom-2 flex flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-panel">
          <button type="button" aria-label="Zoom in" onClick={() => zoomAt(size.w / 2, size.h / 2, 1.3)} className="grid size-8 place-items-center text-text-2 hover:bg-surface-2 hover:text-text"><Plus className="size-4" aria-hidden /></button>
          <button type="button" aria-label="Zoom out" onClick={() => zoomAt(size.w / 2, size.h / 2, 1 / 1.3)} className="grid size-8 place-items-center border-t border-border text-text-2 hover:bg-surface-2 hover:text-text"><Minus className="size-4" aria-hidden /></button>
          <button type="button" aria-label="Fit to view" onClick={() => setT({ x: 0, y: 0, k: 1 })} className="grid size-8 place-items-center border-t border-border text-text-2 hover:bg-surface-2 hover:text-text"><Maximize2 className="size-3.5" aria-hidden /></button>
        </div>
        {nodes.length === 0 && (
          <p className="absolute inset-0 grid place-items-center px-6 text-center text-[13px] text-text-3">
            {params.grew ? "Nothing grew this week." : "Nothing to draw yet."}
          </p>
        )}
      </div>

      <GraphLegend edges={edges} />
    </div>
  );
}

// ---------------------------------------------------------------- legend

const LEGEND: { kind: ViewEdgeKind; label: string; hint: string }[] = [
  { kind: "supersedes", label: "supersedes", hint: "a newer fact replaces an older one, which dims" },
  { kind: "contradicts", label: "contradicts", hint: "two active facts disagree" },
  { kind: "anchored", label: "anchored", hint: "depends on a file; broken when the file changed" },
  { kind: "decided_in", label: "decided in", hint: "came out of a thread of work" },
  { kind: "extends", label: "extends", hint: "adds detail; both stay active" },
  { kind: "derived", label: "derived", hint: "inferred from two or more memories" },
  { kind: "said_by", label: "said by", hint: "who said it" },
];

export function LegendStroke({ kind }: { kind: ViewEdgeKind }) {
  const e: ViewEdge = { from: "a", to: "b", kind, current: kind === "anchored" ? false : undefined };
  return (
    <svg width={34} height={12} aria-hidden className="shrink-0 overflow-visible">
      <Edge e={e} a={{ x: 0, y: 6 }} b={{ x: 34, y: 6 }} ra={0} rb={0} dim={false} />
    </svg>
  );
}

export function GraphLegend({ edges }: { edges: ViewEdge[] }) {
  const count = (k: ViewEdgeKind) => edges.filter((e) => e.kind === k).length;
  return (
    <div className="border-t border-border px-3 py-2.5">
      <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-[11.5px] text-text-2">
        {LEGEND.map((l) => (
          <li key={l.kind} className="inline-flex items-center gap-1.5" title={l.hint} data-legend={l.kind}>
            <LegendStroke kind={l.kind} />
            {l.label}
            <span className="tabular-nums text-text-3">{count(l.kind)}</span>
          </li>
        ))}
      </ul>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-[11.5px] text-text-3">
        <li className="inline-flex items-center gap-1.5"><svg width={14} height={14} aria-hidden><circle cx={7} cy={7} r={5.5} fill="var(--surface-3)" stroke="var(--info)" strokeWidth={2} /></svg>thread</li>
        {(["decision", "rule", "gotcha", "fact", "preference"] as const).map((c) => (
          <li key={c} className="inline-flex items-center gap-1.5"><svg width={10} height={10} aria-hidden><circle cx={5} cy={5} r={4.5} fill={CATEGORY_FILL[c]} /></svg>{c}</li>
        ))}
        <li className="inline-flex items-center gap-1.5"><svg width={12} height={12} aria-hidden><rect x={1} y={1} width={10} height={10} rx={2} fill="var(--surface)" stroke="var(--text-3)" /></svg>file</li>
        <li className="inline-flex items-center gap-1.5"><svg width={14} height={14} aria-hidden><circle cx={7} cy={7} r={6} fill="var(--accent)" opacity={0.25} /><circle cx={7} cy={7} r={3} fill="var(--text-2)" /></svg>grew this week</li>
      </ul>
    </div>
  );
}

