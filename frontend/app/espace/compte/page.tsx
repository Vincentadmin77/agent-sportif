"use client";
import { useState } from "react";
import Gate from "@/components/Gate";
import type { Me } from "@/components/useMe";
import { api, dt } from "@/lib/api";

function Account({ me }: { me: Me }) {
  const [prefs, setPrefs] = useState({ email_alerts: !!me.notification_prefs.email_alerts, live_alerts: !!me.notification_prefs.live_alerts });
  const [saved, setSaved] = useState(false);
  return (
    <>
      <h1>Mon compte</h1>
      <h2>Abonnements</h2>
      <div className="table-scroll"><table><thead><tr><th>Formule</th><th>Statut</th><th>Origine</th><th>Début</th><th>Fin</th></tr></thead>
        <tbody>{me.entitlements.map((e, i) => (<tr key={i}><td>{e.plan_code}</td><td>{e.status}</td><td>{e.source}</td><td>{dt(e.starts_at)}</td><td>{dt(e.ends_at)}</td></tr>))}</tbody></table></div>
      <p className="small muted">La gestion du paiement et la résiliation se font depuis votre compte client de la boutique.</p>
      <h2>Notifications</h2>
      <form className="stack" onSubmit={async (e) => { e.preventDefault(); await api("/me/preferences", { method: "PUT", body: prefs }); setSaved(true); }}>
        <label style={{ display: "flex", gap: 8 }}><input type="checkbox" checked={prefs.email_alerts} onChange={(e) => setPrefs({ ...prefs, email_alerts: e.target.checked })} /> Recevoir les publications par e-mail</label>
        <label style={{ display: "flex", gap: 8 }}><input type="checkbox" checked={prefs.live_alerts} onChange={(e) => setPrefs({ ...prefs, live_alerts: e.target.checked })} /> Recevoir les alertes live</label>
        <button className="btn">Enregistrer</button>{saved && <span className="small muted">Enregistré.</span>}
      </form>
      <p className="small muted">L&apos;envoi effectif des notifications nécessite un service d&apos;e-mail ou de push, pas encore branché.</p>
    </>
  );
}

export default function Page() {
  return <Gate>{(me) => <Account me={me} />}</Gate>;
}
