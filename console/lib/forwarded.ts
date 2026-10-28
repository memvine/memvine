// The browser's address, passed on to the API. The console calls the API from its own address, so
// without this every sign-in would count against one rate-limit budget. The API believes the
// header only from a trusted proxy (MEMVINE_TRUSTED_PROXIES; on Railway the console reaches it over
// the private network) and reads the chain from the right, so a browser cannot choose its address.
import "server-only";
import { headers } from "next/headers";

export function forwardedFrom(incoming: Headers): Record<string, string> {
  const chain = incoming.get("x-forwarded-for");
  return chain ? { "X-Forwarded-For": chain } : {};
}

export async function forwarded(): Promise<Record<string, string>> {
  try {
    return forwardedFrom(await headers());
  } catch {
    return {}; // outside a request (build time)
  }
}
