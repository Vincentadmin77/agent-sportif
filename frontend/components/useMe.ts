"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

export type Me = { id: string; email: string; role: string; features: string[]; notification_prefs: Record<string, boolean>;
  entitlements: { plan_code: string; status: string; source: string; starts_at: string; ends_at: string | null }[] };

export function useMe() {
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "anon" | "ok">("loading");
  useEffect(() => {
    api<Me>("/me").then((m) => { setMe(m); setState("ok"); })
      .catch((e) => setState(e instanceof ApiError && e.status === 401 ? "anon" : "anon"));
  }, []);
  return { me, state };
}

export const can = (me: Me | null, f: string) => !!me && (me.features.includes("*") || me.features.includes(f));
