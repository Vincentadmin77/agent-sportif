"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import Gate from "@/components/Gate";
import { api, dt, num, pct, selectionLabel } from "@/lib/api";

type Sel = { id: string; match_label: string; competition: string; published_at: string; market?: string; selection?: string;
  line?: number | null; odds?: number; bookmaker?: string; odds_observed_at?: string; p_win?: number; fair_odds?: number;
  ev?: number; ev_low?: number; rationale?: string; model_version?: string };
type Current = { access: "full" | "teaser"; count: number; latest_run: { at: string | null; outcome: string } | null; selections: Sel[] };

function Dashboard() {
  const [data, setData] = useState<Current | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Current>("/selections").then(setData).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="error">{err}</p>;
  if (!data) return <p className="muted">Chargement…</p>;
  return (
    <>
      <h1>Sélections du moment</h1>
      {data.latest_run && <p className="muted">Dernière analyse : {dt(data.latest_run.at)} ·{" "}
        {data.latest_run.outcome === "no_opportunity" ? "aucune opportunité ne satisfait les critères" : "opportunités détectées"}</p>}
      {data.count === 0 && <div className="notice">Aucune sélection publiée en ce moment. Aucune opportunité ne satisfait actuellement nos critères.</div>}
      {data.access === "teaser" && data.count > 0 && (
        <div className="notice">{data.count} sélection(s) publiée(s). <Link href="/tarifs">Abonnez-vous</Link> pour voir le détail.</div>
      )}
      <div className="grid">{data.access === "full" && data.selections.map((s) => (
        <div className="card" key={s.id}>
          <div className="muted small">{s.competition} · publiée {dt(s.published_at)}</div>
          <h3>{s.match_label}</h3>
          <p><strong>{selectionLabel({ market: s.market!, selection: s.selection!, line: s.line })}</strong></p>
          <table><tbody>
            <tr><th>Cote observée</th><td className="num">{num(s.odds)} ({s.bookmaker}, {dt(s.odds_observed_at)})</td></tr>
            <tr><th>Probabilité estimée</th><td className="num">{pct(s.p_win)}</td></tr>
            <tr><th>Cote juste du modèle</th><td className="num">{num(s.fair_odds)}</td></tr>
            <tr><th>Espérance / prudente</th><td className="num">{pct(s.ev)} / {pct(s.ev_low)}</td></tr>
          </tbody></table>
          <p className="small muted">{s.rationale}</p>
          <p className="small muted">Modèle {s.model_version}. Vérifiez la cote actuelle avant toute décision : elle a pu changer.</p>
        </div>
      ))}</div>
    </>
  );
}

export default function Page() {
  return <Gate>{() => <Dashboard />}</Gate>;
}
