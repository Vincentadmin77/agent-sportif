export default function Page() {
  return (
    <div className="prose">
      <h1>La plateforme</h1>
      <p className="lead">Un produit d&apos;analyse, pas un générateur de pronostics.</p>
      <h2>Ce que fait le système</h2>
      <ul>
        <li>Collecte des matchs, résultats et cotes auprès de fournisseurs disposant des droits nécessaires.</li>
        <li>Estime les probabilités de chaque marché avec un modèle statistique versionné et testé hors échantillon.</li>
        <li>Compare ces probabilités aux meilleures cotes observées, retire la marge du bookmaker et mesure l&apos;incertitude.</li>
        <li>Soumet chaque opportunité à des seuils de qualité, puis à une validation humaine avant publication.</li>
        <li>Enregistre tout : sélections publiées, opportunités écartées, corrections, résultats.</li>
      </ul>
      <h2>Ce que le système ne fait pas</h2>
      <ul>
        <li>Il ne promet aucun gain et ne présente jamais une probabilité comme une certitude.</li>
        <li>Il n&apos;invente jamais de statistiques ni de cotes pour combler une donnée manquante.</li>
        <li>Il ne publie pas pour remplir un quota.</li>
      </ul>
    </div>
  );
}
