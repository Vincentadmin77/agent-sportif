"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, dt, num, selectionLabel } from "@/lib/api";

type Sel = { id: string; match_label: string; market?: string; selection?: string; line?: number | null; odds?: number; result: string | null; published_at: string | null; profit?: number | null };

export default function Analyses() {
  const [rows, setRows] = useState<Sel[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Sel[]>("/public/history?limit=20").then(setRows).catch((e) => setErr(e.message)); }, []);
  return (
    <>
      <h1>Analyses</h1>
      <p className="lead">Les sélections en cours et leurs analyses détaillées sont réservées aux abonnés. L&apos;historique des publications est public.</p>
      <div className="row" style={{ margin: "16px 0 24px" }}>
        <Link className="btn" href="/espace">Accéder à mes sélections</Link>
        <Link className="btn secondary" href="/tarifs">Voir les formules</Link>
      </div>
      <h2>Dernières publications</h2>
      {err && <p className="error">{err}</p>}
      {rows && rows.length === 0 && <p className="muted">Aucune sélection réelle publiée pour le moment.</p>}
      {rows && rows.length > 0 && (
        <div className="table-scroll"><table>
          <thead><tr><th>Publiée</th><th>Match</th><th>Marché</th><th className="num">Cote</th><th>Résultat</th></tr></thead>
          <tbody>{rows.map((s) => (
            <tr key={s.id}><td>{dt(s.published_at)}</td><td>{s.match_label}</td>
              <td>{s.market ? selectionLabel({ market: s.market, selection: s.selection!, line: s.line }) : "—"}</td>
              <td className="num">{num(s.odds)}</td><td>{s.result ?? "en cours"}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
