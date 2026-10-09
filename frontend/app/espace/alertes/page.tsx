"use client";
import { useEffect, useState } from "react";
import Gate from "@/components/Gate";
import { can } from "@/components/useMe";
import { api, dt, num, pct } from "@/lib/api";

type Alert = { id: number; kind: string; commercial: boolean; match_label: string; minute: number; score: string; market: string | null;
  odds: number | null; bookmaker: string | null; probability: number | null; fair_odds: number | null; ev: number | null;
  data_updated_at: string; reasons: string[]; invalidation: string[]; created_at: string };
const KIND: Record<string, string> = { opportunity: "Opportunité", favourite_conceded_first: "Le favori encaisse le premier but",
  dominant_but_trailing: "Domine mais mené", stale_data: "Données trop anciennes" };

function Alerts() {
  const [rows, setRows] = useState<Alert[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api<Alert[]>("/alerts").then(setRows).catch((e) => setErr(e.message));
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);
  if (err) return <p className="error">{err}</p>;
  if (!rows) return <p className="muted">Chargement…</p>;
  if (!rows.length) return <p className="muted">Aucune alerte récente.</p>;
  return (<div className="grid">{rows.map((a) => (
    <div className="card" key={a.id}>
      <div className="row"><span className={`badge ${a.commercial ? "ok" : ""}`}>{KIND[a.kind] ?? a.kind}</span>
        <span className="muted small">{a.minute.toFixed(0)}&apos; · {a.score}</span></div>
      <h3 style={{ marginTop: 8 }}>{a.match_label}</h3>
      {a.market && <p className="small">{a.market} · cote {num(a.odds)} ({a.bookmaker}) · proba {pct(a.probability)} · cote juste {num(a.fair_odds)} · EV {pct(a.ev)}</p>}
      <ul className="small">{a.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      {a.invalidation.length > 0 && <p className="small muted">Invalidée si : {a.invalidation.join(" ; ")}</p>}
      <p className="small muted">Données du {dt(a.data_updated_at)}</p>
    </div>))}</div>);
}

export default function Page() {
  return <Gate>{(me) => (<><h1>Alertes live</h1>
    {can(me, "live.alerts") ? <Alerts /> : <p>Réservé à la formule Alertes live.</p>}</>)}</Gate>;
}
