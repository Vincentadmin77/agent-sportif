export default function Page() {
  return (
    <div className="prose">
      <h1>Méthodologie</h1>
      <h2>1. Estimer les probabilités</h2>
      <p>Chaque équipe reçoit une force offensive et défensive estimée sur ses matchs passés, les plus récents pesant davantage. Le modèle (Dixon-Coles) produit la probabilité de chaque score, ce qui donne la probabilité de chaque marché.</p>
      <h2>2. Mesurer l&apos;incertitude</h2>
      <p>Les paramètres du modèle sont incertains. Nous en tirons un intervalle de probabilité et retenons un scénario prudent (10e centile).</p>
      <h2>3. Comparer au marché</h2>
      <ul>
        <li>Probabilité implicite brute : P = 1 / cote.</li>
        <li>Cote juste du modèle : 1 / p (ou 1 + p<sub>perte</sub> / p<sub>gain</sub> si remboursement possible).</li>
        <li>Espérance par unité misée : EV = p × cote − 1.</li>
        <li>Probabilité de marché sans marge : méthode de Shin appliquée à chaque bookmaker puis moyennée.</li>
      </ul>
      <h2>4. Filtrer</h2>
      <p>Une opportunité n&apos;est retenue que si : l&apos;espérance centrale dépasse un seuil, l&apos;espérance prudente reste positive, l&apos;avantage survit à une baisse de 2 points de probabilité, la cote est récente, les données sont de qualité suffisante, et l&apos;écart avec le marché n&apos;est pas anormalement grand (ce qui signale plutôt une information manquante qu&apos;une affaire). Une seule sélection par match. Au plus quatre, souvent zéro.</p>
      <h2>5. Évaluer honnêtement</h2>
      <p>Le modèle est évalué par des tests chronologiques hors échantillon (score de Brier, log-loss, calibration). Sur environ 10 000 matchs des cinq grands championnats (2020-2026), il prédit nettement mieux qu&apos;une référence naïve. Cela ne prouve pas une rentabilité : seule la comparaison avec les cotes de clôture sur un large échantillon peut l&apos;établir.</p>
    </div>
  );
}
