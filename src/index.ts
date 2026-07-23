/** memvine public API — use the CLI (`memvine`) or MCP server for normal use. */
export { Store, StoreConfig, DIR_NAME } from "./store.js";
export { Memory, MemoryMeta, MemoryKind, MemoryStatus, KINDS } from "./schema.js";
export { findStale, markStale, StaleReport } from "./staleness.js";
export { buildDigest, compileInto } from "./compile.js";
export { serve } from "./mcp.js";
