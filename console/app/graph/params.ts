// The graph's state in the URL: the selected node, open work or everything, and growth only.
export interface GraphParams {
  node?: string;
  view?: "all";
  grew?: boolean;
}

export function parseGraphParams(search: Record<string, string | undefined>): GraphParams {
  return {
    node: search.node || undefined,
    view: search.view === "all" ? "all" : undefined,
    grew: search.grew === "1" ? true : undefined,
  };
}

export function graphHref(base: string, p: GraphParams): string {
  const q = new URLSearchParams();
  if (p.view) q.set("view", p.view);
  if (p.grew) q.set("grew", "1");
  if (p.node) q.set("node", p.node);
  const s = q.toString();
  return s ? `${base}?${s}` : base;
}
