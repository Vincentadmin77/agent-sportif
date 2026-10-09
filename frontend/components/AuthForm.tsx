"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [adult, setAdult] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api(mode === "login" ? "/auth/login" : "/auth/register", { method: "POST", body: { email, password } });
      router.push("/espace");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <label>E-mail<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></label>
      <label>Mot de passe (10 caractères minimum)
        <input type="password" required minLength={10} value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
      {mode === "register" && (
        <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} required />
          Je certifie avoir 18 ans ou plus.
        </label>
      )}
      {err && <p className="error">{err}</p>}
      <button className="btn" disabled={busy}>{mode === "login" ? "Se connecter" : "Créer mon compte"}</button>
    </form>
  );
}
