"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Plan = { code: string; name: string; features: string[]; price: string };
const FEATURE_LABELS: Record<string, string> = {
  "public.history": "Historique public des publications",
  "football.teaser": "Nombre de sélections du jour",
  "football.selections": "Sélections football complètes",
  "football.analysis": "Analyses détaillées des matchs",
  "basketball.selections": "Sélections basket (à venir)",
  "basketball.analysis": "Analyses basket (à venir)",
  "tennis.selections": "Sélections tennis (à venir)",
  "tennis.analysis": "Analyses tennis (à venir)",
  "live.alerts": "Alertes live",
};

export default function Tarifs() {
  const [plans, setPlans] = useState<Plan[]>([]);
  useEffect(() => { api<Plan[]>("/public/plans").then(setPlans).catch(() => setPlans([])); }, []);
  return (
    <>
      <h1>Tarifs et abonnements</h1>
      <p className="lead">Les abonnements sont vendus via notre boutique Shopify. Votre accès est activé automatiquement sur le compte associé au même e-mail.</p>
      <div className="notice">Un abonnement donne accès à des analyses. Il ne garantit aucun gain.</div>
      <div className="grid">{plans.map((p) => (
        <div className="card" key={p.code}>
          <h3>{p.name}</h3>
          <div className="muted small">{p.price}</div>
          <ul className="small">{p.features.map((f) => <li key={f}>{FEATURE_LABELS[f] ?? f}</li>)}</ul>
        </div>
      ))}</div>
    </>
  );
}
