export default function Page() {
  return (
    <div className="prose">
      <h1>Politique de confidentialité</h1>
      <div className="notice">Document à compléter et à faire valider juridiquement avant la commercialisation.</div>
      <p>{"Données traitées : e-mail, mot de passe (stocké sous forme hachée), préférences, historique d'abonnement reçu de Stripe ou PayPal. Aucune donnée bancaire n'est stockée par la plateforme. Base légale, durées de conservation, sous-traitants (hébergeur, Stripe, PayPal, fournisseurs de données, Anthropic pour l'analyse de textes publics) et droits RGPD (accès, rectification, effacement, portabilité, réclamation auprès de la CNIL) à compléter avant le lancement."}</p>
    </div>
  );
}
