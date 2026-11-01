import type { ReactNode } from "react";
import type { Viewer } from "../types/contracts";

/**
 * Renders its children only for the circle organiser. A UI guard on top of the
 * organiser-only API call, not instead of it: the backend must also refuse members.
 */
export function OrganiserOnly({ viewer, children }: { viewer: Viewer; children: ReactNode }) {
  return viewer.role === "organiser" ? <>{children}</> : null;
}
