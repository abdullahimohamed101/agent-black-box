"use client";

import { createContext, useContext, useEffect, type ReactNode } from "react";
import { setApiWorkspace } from "@/lib/api/client";
import { projectFilter } from "@/lib/routes";
import type { WorkspaceContext } from "@/lib/workspace";

/**
 * The resolved workspace for this page, handed down from the server (`loadWorkspace`). `permissions` come from the
 * API (`/v1/me`); this file only reads them, it never maps a role to rights (the API stays the authority).
 */
const Ctx = createContext<WorkspaceContext | null>(null);

export function WorkspaceProvider({
  value,
  children,
}: {
  value: WorkspaceContext;
  children: ReactNode;
}) {
  // Set while rendering as well as in the effect: child effects (the first queries) run before this component's.
  // The value is the same for the life of the page, so the render-time write is idempotent.
  setApiWorkspace(value.workspace.id);
  useEffect(() => {
    setApiWorkspace(value.workspace.id);
    return () => setApiWorkspace(null);
  }, [value.workspace.id]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useWorkspaceOptional = (): WorkspaceContext | null => useContext(Ctx);

export function useWorkspace(): WorkspaceContext {
  const value = useContext(Ctx);
  if (!value) throw new Error("useWorkspace outside a workspace page");
  return value;
}

/** True when the API listed the permission for this person in this workspace (`/v1/me`). */
export function useCan(permission: string): boolean {
  return useContext(Ctx)?.permissions.includes(permission) ?? false;
}

/** The API's project id for a route segment: `all` is no filter, a slug (or an id) is looked up (KI-027). */
export function useProjectId(segment: string): string | null {
  const ctx = useContext(Ctx);
  const filter = projectFilter(segment);
  if (filter === null || !ctx) return filter;
  return ctx.projects.find((p) => p.slug === filter || p.id === filter)?.id ?? filter;
}
