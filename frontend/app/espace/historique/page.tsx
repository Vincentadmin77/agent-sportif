"use client";
import { useEffect, useState } from "react";
import Gate from "@/components/Gate";
import { can } from "@/components/useMe";
import { api, dt, num, pct, selectionLabel } from "@/lib/api";

type Row = { id: string; kind: string; match_label: string; market: string; selection: string; line: number | null; odds: number;
  bookmaker: string; p_win: number; published_at: string; result: string | null; profit: number | null;
  corrections: { field: string; old_value: string; new_value: string; reason: string; at: string }[] };

function History() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Row[]>("/selections/history").then(setRows).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="error">{err}</p>;
  if (!rows) return <p className="muted">Chargement…</p>;
  if (!rows.length) return <p className="muted">Aucune publication pour le moment.</p>;
  return (
    <div className="table-scroll"><table>
      <thead><tr><th>Publiée</th><th>Type</th><th>Match</th><th>Marché</th><th className="num">Cote</th><th className="num">Proba.</th><th>Résultat</th><th className="num">Gain/unité</th><th>Corrections</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.id}><td>{dt(r.published_at)}</td><td>{r.kind === "live" ? "Live" : "Avant-match"}</td><td>{r.match_label}</td>
          <td>{selectionLabel(r)}</td><td className="num">{num(r.odds)}</td><td className="num">{pct(r.p_win)}</td>
          <td>{r.result ?? "en cours"}</td><td className="num">{num(r.profit)}</td>
          <td className="small">{r.corrections.map((c, i) => <div key={i}>{dt(c.at)} · {c.field} : {c.old_value} → {c.new_value} ({c.reason})</div>)}</td></tr>
      ))}</tbody>
    </table></div>
  );
}

export default function Page() {
  return <Gate>{(me) => (<><h1>Historique des publications</h1>
    {can(me, "football.selections") ? <History /> : <p>Réservé aux abonnés Football Premium.</p>}</>)}</Gate>;
}
