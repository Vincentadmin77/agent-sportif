import AuthForm from "@/components/AuthForm";

export default function Page() {
  return (<><h1>Créer un compte</h1>
    <p className="muted">Si vous avez déjà acheté un abonnement sur la boutique, utilisez le même e-mail : l&apos;accès sera activé automatiquement.</p>
    <AuthForm mode="register" /></>);
}
