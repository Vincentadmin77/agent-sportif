"use client";
import { useCallback, useEffect, useState } from "react";
import Gate from "@/components/Gate";
import { api, dt, num, pct, selectionLabel } from "@/lib/api";

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const TABS = ["Analyses", "Sélections", "Réglages", "Utilisateurs", "Sources", "Paiements", "Performances", "Journal"] as const;

function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const reload = useCallback(() => { api<T>(path).then((d) => { setData(d); setErr(null); }).catch((e) => setErr(e.message)); }, [path]);
  useEffect(reload, [reload]);
  return { data, err, reload };
}

function ModeBadge({ mode }: { mode: string }) {
  return <span className={`badge ${mode === "real" ? "ok" : "warn"}`}>{mode === "real" ? "réel" : "SIMULATION"}</span>;
}

function Runs() {
  const { data, err, reload } = useLoad<Any[]>("/admin/runs");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [comp, setComp] = useState("FR1");
  async function launch(mode: "simulation" | "real") {
    setBusy(true); setMsg(null);
    try { const r = await api<Any>("/admin/runs/prematch", { method: "POST", body: { mode, competition: comp } });
      setMsg(`Analyse ${r.status}${r.outcome ? ` · ${r.outcome}` : ""}${r.status_detail ? ` · ${r.status_detail}` : ""}`); reload(); setOpen(r.id);
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  return (<>
    <div className="row">
      <select value={comp} onChange={(e) => setComp(e.target.value)}>{["FR1", "EN1", "ES1", "DE1", "IT1", "UCL"].map((c) => <option key={c}>{c}</option>)}</select>
      <button className="btn" disabled={busy} onClick={() => launch("real")}>Lancer une analyse réelle</button>
      <button className="btn secondary" disabled={busy} onClick={() => launch("simulation")}>Lancer une simulation</button>
    </div>
    {msg && <p className="small">{msg}</p>}
    {err && <p className="error">{err}</p>}
    <div className="table-scroll"><table><thead><tr><th>Début</th><th>Compétition</th><th>Mode</th><th>Statut</th><th>Issue</th><th>Modèle</th><th /></tr></thead>
      <tbody>{data?.map((r) => (<tr key={r.id}><td>{dt(r.started_at)}</td><td>{r.competition}</td><td><ModeBadge mode={r.mode} /></td>
        <td>{r.status}{r.status_detail && <div className="small muted">{r.status_detail}</div>}</td><td>{r.outcome ?? "—"}</td>
        <td>{r.model_name} {r.model_version}</td><td><button className="btn secondary small" onClick={() => setOpen(r.id)}>Détail</button></td></tr>))}</tbody></table></div>
    {open && <RunDetail id={open} />}
  </>);
}

function RunDetail({ id }: { id: string }) {
  const { data, err } = useLoad<Any>(`/admin/runs/${id}`);
  if (err) return <p className="error">{err}</p>;
  if (!data) return null;
  return (<div className="card" style={{ marginTop: 16 }}>
    <h3>Analyse {data.competition} <ModeBadge mode={data.mode} /></h3>
    {data.mode !== "real" && <div className="notice">Données simulées : ces résultats ne peuvent pas être publiés.</div>}
    <p className="small muted">Sources : {JSON.stringify(data.data_sources)} · issue : {data.outcome ?? "—"}</p>
    <h3>Matchs</h3>
    <div className="table-scroll"><table><thead><tr><th>Match</th><th>Coup d&apos;envoi</th><th className="num">xG modèle</th><th className="num">1 / N / 2</th><th>Remarque</th></tr></thead>
      <tbody>{(data.fixtures as Any[]).map((f) => (<tr key={f.match_id}><td>{f.match}</td><td>{dt(f.kickoff)}</td>
        <td className="num">{f.expected_goals ? `${num(f.expected_goals[0])} – ${num(f.expected_goals[1])}` : "—"}</td>
        <td className="num">{f.probabilities["1x2:home"] != null ? `${pct(f.probabilities["1x2:home"], 0)} / ${pct(f.probabilities["1x2:draw"], 0)} / ${pct(f.probabilities["1x2:away"], 0)}` : "—"}</td>
        <td className="small">{f.skipped_reason ?? `${f.n_quotes} cotes`}</td></tr>))}</tbody></table></div>
    <h3 style={{ marginTop: 16 }}>Opportunités examinées ({data.candidates.length})</h3>
    <div className="table-scroll"><table><thead><tr><th>Match</th><th>Marché</th><th className="num">Cote</th><th className="num">Proba (p10)</th><th className="num">Cote juste</th><th className="num">EV / prudente / stress</th><th className="num">Marché</th><th className="num">Qualité</th><th>Décision</th></tr></thead>
      <tbody>{(data.candidates as Any[]).map((c) => (<tr key={c.id}><td>{c.match_label}</td><td>{selectionLabel(c as { market: string; selection: string; line: number | null })}</td>
        <td className="num">{num(c.odds)}<div className="small muted">{c.bookmaker}</div></td><td className="num">{pct(c.p_win)} ({pct(c.p_low)})</td>
        <td className="num">{num(c.fair_odds)}</td><td className="num">{pct(c.ev)} / {pct(c.ev_low)} / {pct(c.ev_stress)}</td>
        <td className="num">{pct(c.market_fair_prob)}</td><td className="num">{num(c.data_quality)}</td>
        <td className="small">{c.accepted ? <span className="badge ok">retenue</span> : (c.reasons as string[]).join(" ; ")}</td></tr>))}</tbody></table></div>
  </div>);
}

function Selections() {
  const { data, err, reload } = useLoad<Any[]>("/admin/selections");
  const [msg, setMsg] = useState<string | null>(null);
  async function act(id: string, body: Any) {
    setMsg(null);
    try { await api(`/admin/selections/${id}`, { method: "POST", body }); reload(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  async function review(id: string) {
    const text = prompt("Collez les informations récentes (une par ligne : source | titre | texte)") ?? "";
    const news = text.split("\n").filter(Boolean).map((l) => { const [source, title, ...rest] = l.split("|"); return { source: (source ?? "").trim(), title: (title ?? "").trim(), text: rest.join("|").trim() }; });
    setMsg(null);
    try { await api(`/admin/selections/${id}/review`, { method: "POST", body: { news } }); reload(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  return (<>
    {err && <p className="error">{err}</p>}{msg && <p className="error">{msg}</p>}
    <div className="table-scroll"><table><thead><tr><th>Créée</th><th>Mode</th><th>Statut</th><th>Match</th><th>Marché</th><th className="num">Cote</th><th className="num">Proba / EV</th><th>Contrôle IA</th><th>Actions</th></tr></thead>
      <tbody>{data?.map((s) => (<tr key={s.id}><td>{dt(s.published_at ?? s.odds_observed_at)}</td><td><ModeBadge mode={s.mode} /></td><td>{s.status}{s.result && ` · ${s.result}`}</td>
        <td>{s.match_label}</td><td>{selectionLabel(s as { market: string; selection: string; line: number | null })}</td><td className="num">{num(s.odds)}<div className="small muted">{s.bookmaker}</div></td>
        <td className="num">{pct(s.p_win)} / {pct(s.ev)}</td>
        <td className="small">{s.qc_review ? `${s.qc_review.verdict} : ${s.qc_review.summary}` : "—"}</td>
        <td><div className="row">
          {s.mode === "real" && ["pending_review", "suspended"].includes(s.status) && <button className="btn small" onClick={() => act(s.id, { action: "publish" })}>Publier</button>}
          {s.mode === "real" && s.status === "pending_review" && <button className="btn secondary small" onClick={() => review(s.id)}>Contrôle IA</button>}
          {["published", "pending_review"].includes(s.status) && <button className="btn secondary small" onClick={() => { const r = prompt("Motif de la suspension"); if (r) act(s.id, { action: "suspend", reason: r }); }}>Suspendre</button>}
          {["pending_review", "suspended"].includes(s.status) && <button className="btn secondary small" onClick={() => { const r = prompt("Motif du rejet"); if (r) act(s.id, { action: "reject", reason: r }); }}>Rejeter</button>}
          {s.status === "published" && ["win", "lose", "push", "void"].map((r) => <button key={r} className="btn secondary small" onClick={() => act(s.id, { action: "settle", result: r })}>{r}</button>)}
        </div></td></tr>))}</tbody></table></div>
  </>);
}

function Settings() {
  const { data, err, reload } = useLoad<Any>("/admin/settings/selection");
  const comps = useLoad<Any[]>("/admin/competitions");
  const [draft, setDraft] = useState<Any | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (data) setDraft(data); }, [data]);
  if (err) return <p className="error">{err}</p>;
  if (!draft) return null;
  const numeric = Object.keys(draft).filter((k) => typeof draft[k] === "number");
  const markets = ["1x2", "double_chance", "dnb", "totals", "btts"];
  return (<div className="grid">
    <form className="card stack" onSubmit={async (e) => { e.preventDefault(); setMsg(null);
      try { await api("/admin/settings/selection", { method: "PUT", body: draft }); setMsg("Enregistré."); reload(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } }}>
      <h3>Seuils de sélection</h3>
      {numeric.map((k) => <label key={k}>{k}<input type="number" step="any" value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: Number(e.target.value) })} /></label>)}
      <div><div className="small muted">Marchés actifs</div>{markets.map((m) => (
        <label key={m} style={{ display: "flex", gap: 8 }}><input type="checkbox" checked={draft.enabled_markets.includes(m)}
          onChange={(e) => setDraft({ ...draft, enabled_markets: e.target.checked ? [...draft.enabled_markets, m] : draft.enabled_markets.filter((x: string) => x !== m) })} />{m}</label>))}</div>
      <button className="btn">Enregistrer</button>{msg && <span className="small">{msg}</span>}
    </form>
    <div className="card"><h3>Compétitions surveillées</h3>
      {comps.data?.map((c) => (<label key={c.code} style={{ display: "flex", gap: 8 }}>
        <input type="checkbox" checked={c.monitored} onChange={async (e) => { await api(`/admin/competitions/${c.code}`, { method: "PUT", body: { monitored: e.target.checked } }); comps.reload(); }} />
        {c.name} ({c.code})</label>))}</div>
  </div>);
}

function Users() {
  const { data, err, reload } = useLoad<Any[]>("/admin/users");
  return (<>{err && <p className="error">{err}</p>}
    <div className="table-scroll"><table><thead><tr><th>E-mail</th><th>Rôle</th><th>Actif</th><th>Droits</th><th>Actions</th></tr></thead>
      <tbody>{data?.map((u) => (<tr key={u.id}><td>{u.email}</td><td>{u.role}</td><td>{u.is_active ? "oui" : "non"}</td>
        <td className="small">{(u.entitlements as Any[]).map((e) => `${e.plan_code} (${e.status})`).join(", ")}</td>
        <td><div className="row">
          <button className="btn secondary small" onClick={async () => { const p = prompt("Code de la formule (ex. football_premium)"); if (p) { await api(`/admin/users/${u.id}/entitlements`, { method: "POST", body: { plan_code: p } }).catch((e) => alert(e.message)); reload(); } }}>Ajouter un droit</button>
          <button className="btn secondary small" onClick={async () => { await api(`/admin/users/${u.id}`, { method: "PUT", body: { role: u.role === "admin" ? "user" : "admin", is_active: u.is_active } }).catch((e) => alert(e.message)); reload(); }}>{u.role === "admin" ? "Retirer admin" : "Passer admin"}</button>
        </div></td></tr>))}</tbody></table></div></>);
}

function Json({ path }: { path: string }) {
  const { data, err } = useLoad<Any>(path);
  if (err) return <p className="error">{err}</p>;
  return <pre className="card small" style={{ overflowX: "auto" }}>{JSON.stringify(data, null, 2)}</pre>;
}

function Admin() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Analyses");
  return (<>
    <h1>Administration</h1>
    <div className="row" style={{ marginBottom: 16 }}>{TABS.map((t) => (
      <button key={t} className={`btn small ${t === tab ? "" : "secondary"}`} onClick={() => setTab(t)}>{t}</button>))}</div>
    {tab === "Analyses" && <Runs />}
    {tab === "Sélections" && <Selections />}
    {tab === "Réglages" && <Settings />}
    {tab === "Utilisateurs" && <Users />}
    {tab === "Sources" && <Json path="/admin/providers" />}
    {tab === "Paiements" && <Json path="/admin/payments" />}
    {tab === "Performances" && <Json path="/admin/performance" />}
    {tab === "Journal" && <Json path="/admin/audit" />}
  </>);
}

export default function Page() {
  return <Gate>{(me) => (me.role === "admin" ? <Admin /> : <p>Accès réservé aux administrateurs.</p>)}</Gate>;
}
