// Thin client for the Python API (proxied under /api). The server decides
// every permission; the UI only reflects the answers it gets.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: init?.method ?? "GET",
    credentials: "same-origin",
    headers: init?.body !== undefined ? { "content-type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail ?? res.statusText));
  }
  return data as T;
}

export const pct = (x: number | null | undefined, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)} %`);
export const num = (x: number | null | undefined, d = 2) => (x == null ? "—" : x.toFixed(d));
export const dt = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "—";

export const MARKET_LABELS: Record<string, string> = {
  "1x2": "Résultat",
  double_chance: "Double chance",
  dnb: "Remboursé si nul",
  totals: "Total de buts",
  btts: "Les deux équipes marquent",
};

export function selectionLabel(s: { market: string; selection: string; line?: number | null }) {
  const sel: Record<string, string> = {
    home: "Domicile", away: "Extérieur", draw: "Nul", over: "Plus de", under: "Moins de", yes: "Oui", no: "Non",
  };
  const base = `${MARKET_LABELS[s.market] ?? s.market} · ${sel[s.selection] ?? s.selection}`;
  return s.line != null ? `${base} ${s.line}` : base;
}
