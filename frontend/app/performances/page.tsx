"use client";
import { useEffect, useState } from "react";
import { api, dt, num, pct } from "@/lib/api";

type Perf = { n: number; hit_rate?: number; roi?: number; roi_ci95?: [number, number]; avg_odds?: number; period?: [string, string]; note: string; method?: string; by_market: Record<string, { n: number; profit: number; wins: number }> };

export default function Performances() {
  const [p, setP] = useState<Perf | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Perf>("/public/performance").then(setP).catch((e) => setErr(e.message)); }, []);
  return (
    <>
      <h1>Historique des performances</h1>
      <p className="lead">Toutes les sélections réelles publiées et réglées, perdantes comprises, à la cote observée au moment de la publication.</p>
      {err && <p className="error">{err}</p>}
      {p && p.n === 0 && <div className="notice">{p.note}</div>}
      {p && p.n > 0 && (
        <>
          <div className="grid">
            <div className="card"><div className="muted small">Sélections réglées</div><div className="kpi">{p.n}</div></div>
            <div className="card"><div className="muted small">Taux de réussite</div><div className="kpi">{pct(p.hit_rate)}</div></div>
            <div className="card"><div className="muted small">ROI par unité</div><div className="kpi">{pct(p.roi)}</div>
              <div className="muted small">IC 95 % : {pct(p.roi_ci95?.[0])} à {pct(p.roi_ci95?.[1])}</div></div>
            <div className="card"><div className="muted small">Cote moyenne</div><div className="kpi">{num(p.avg_odds)}</div></div>
          </div>
          <div className="notice">
            {p.note === "sample too small to conclude"
              ? "Échantillon trop petit ou intervalle de confiance incluant zéro : aucune conclusion sur la rentabilité n'est possible."
              : "L'intervalle de confiance exclut zéro sur cet échantillon. Cela ne garantit pas les résultats futurs."}
            {p.period && <> Période : {dt(p.period[0])} → {dt(p.period[1])}.</>}
          </div>
          <p className="muted small">{p.method}</p>
        </>
      )}
    </>
  );
}
