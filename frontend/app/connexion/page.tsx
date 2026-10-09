import Link from "next/link";
import AuthForm from "@/components/AuthForm";

export default function Page() {
  return (<><h1>Connexion</h1><AuthForm mode="login" /><p className="small">Pas de compte ? <Link href="/inscription">Créer un compte</Link></p></>);
}
