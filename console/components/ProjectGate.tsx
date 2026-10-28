"use client";
import type { ReactNode } from "react";
import { usePathname } from "next/navigation";

// Pages that make sense before an organization has any project.
const ORG_PAGES = new Set(["/projects", "/settings"]);

/** Shows the first-project prompt instead of a project page while the organization has none.
 *  It decides on every navigation: the root layout stays mounted across client-side navigation,
 *  so a decision made there from the request's path goes stale. */
export function ProjectGate({ hasProjects, fallback, children }: { hasProjects: boolean; fallback: ReactNode; children: ReactNode }) {
  const path = usePathname();
  return <>{hasProjects || ORG_PAGES.has(path) ? children : fallback}</>;
}
