"use client";
import Link from "next/link";
import SpaceNav from "./SpaceNav";
import { Me, useMe } from "./useMe";

export default function Gate({ children }: { children: (me: Me) => React.ReactNode }) {
  const { me, state } = useMe();
  if (state === "loading") return <p className="muted">Chargement…</p>;
  if (!me) return (<><h1>Mon espace</h1><p>Connectez-vous pour accéder à votre espace.</p>
    <Link className="btn" href="/connexion">Connexion</Link></>);
  return (<><SpaceNav me={me} />{children(me)}</>);
}
