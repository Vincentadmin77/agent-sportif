"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { Me } from "./useMe";

export default function SpaceNav({ me }: { me: Me }) {
  const router = useRouter();
  return (
    <div className="row" style={{ marginBottom: 24 }}>
      <Link href="/espace">Tableau de bord</Link>
      <Link href="/espace/historique">Historique</Link>
      <Link href="/espace/alertes">Alertes live</Link>
      <Link href="/espace/compte">Compte</Link>
      {me.role === "admin" && <Link href="/admin">Administration</Link>}
      <span className="spacer" style={{ flex: 1 }} />
      <span className="muted small">{me.email}</span>
      <button className="btn secondary small" onClick={async () => { await api("/auth/logout", { method: "POST", body: {} }); router.push("/"); }}>Déconnexion</button>
    </div>
  );
}
