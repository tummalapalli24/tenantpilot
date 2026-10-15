"use client";

import { createContext, useContext } from "react";

import type { TenantDetail } from "@/lib/types";

export interface TenantState {
  tenant: TenantDetail;
  reload: () => void;
}

export const TenantCtx = createContext<TenantState | null>(null);

export function useTenant(): TenantState {
  const value = useContext(TenantCtx);
  if (!value) throw new Error("useTenant must be used inside the tenant layout");
  return value;
}
