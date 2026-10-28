import { GraphHome } from "./GraphHome";

/** The graph at its own address, for links that should land on it whatever the home becomes. */
export default async function GraphPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  return <GraphHome search={await searchParams} base="/graph" />;
}
