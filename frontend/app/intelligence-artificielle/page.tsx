export default function Page() {
  return (
    <div className="prose">
      <h1>Fonctionnement de l&apos;IA</h1>
      <p className="lead">Deux rôles séparés : le modèle statistique calcule, l&apos;IA de langage vérifie et explique.</p>
      <h2>Le moteur statistique</h2>
      <p>Les probabilités sont produites par un modèle mathématique déterministe et reproductible. Le même jeu de données donne toujours le même résultat, et chaque version du modèle est conservée.</p>
      <h2>Les agents IA</h2>
      <p>Un agent de contrôle qualité lit les informations récentes (compositions, absences, déclarations), distingue les faits vérifiés des rumeurs et peut signaler ou recommander d&apos;écarter une sélection. Il ne peut jamais augmenter une probabilité ni modifier un calcul.</p>
      <p>Les contenus récupérés sur le web sont traités comme des données non fiables : ils ne peuvent pas modifier les règles du système.</p>
      <h2>La validation humaine</h2>
      <p>Au lancement, chaque sélection est relue et validée avant publication.</p>
    </div>
  );
}
