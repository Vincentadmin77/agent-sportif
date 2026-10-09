const QA: [string, string][] = [
  ["Pourquoi certains jours sans sélection ?", "Parce qu'aucune opportunité n'a passé nos seuils de qualité. Publier pour publier serait contraire à la méthode."],
  ["Une probabilité de 70 % veut-elle dire que le pari est bon ?", "Non. Si la cote est de 1,35, l'espérance est négative (0,70 × 1,35 − 1 = −5,5 %). Ce qui compte est l'écart entre probabilité et prix."],
  ["Les résultats sont-ils garantis ?", "Non. Le sport reste incertain et un avantage statistique ne se mesure que sur un grand nombre de sélections."],
  ["D'où viennent les données ?", "De fournisseurs disposant des droits de diffusion (statistiques et cotes multi-bookmakers). Nous ne collectons pas de données sans autorisation."],
  ["Pourquoi l'historique contient-il des sélections perdantes ?", "Parce qu'un historique honnête les inclut toutes. Nous ne supprimons ni ne modifions les sélections publiées ; les corrections sont tracées."],
  ["Comment résilier ?", "Depuis votre compte client Shopify. Votre accès reste actif jusqu'à la fin de la période payée."],
];

export default function Faq() {
  return (
    <div className="prose">
      <h1>FAQ</h1>
      {QA.map(([q, a]) => (<div key={q}><h3 style={{ marginTop: 24 }}>{q}</h3><p>{a}</p></div>))}
    </div>
  );
}
