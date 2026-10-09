import Link from "next/link";

export default function Home() {
  return (
    <>
      <h1>Moins de sélections. Mieux argumentées.</h1>
      <p className="lead">
        SportAI confronte les récits du football aux données. Un moteur statistique indépendant estime les probabilités,
        les compare aux cotes réellement disponibles et ne retient que de 0 à 4 opportunités par période, uniquement
        lorsqu&apos;un avantage résiste aux tests d&apos;incertitude.
      </p>
      <div className="row" style={{ margin: "24px 0 40px" }}>
        <Link className="btn" href="/analyses">Voir les analyses</Link>
        <Link className="btn secondary" href="/methodologie">Comprendre la méthode</Link>
      </div>
      <div className="grid">
        <div className="card">
          <h3>Zéro sélection est une réponse valable</h3>
          <p className="muted">La limite de quatre est un plafond, pas un objectif. Sans avantage robuste, nous publions « aucune opportunité ».</p>
        </div>
        <div className="card">
          <h3>Probabilité ≠ valeur</h3>
          <p className="muted">Une cote de 1,50 n&apos;est pas « sûre ». Seul compte l&apos;écart entre la probabilité estimée et le prix, après prise en compte de l&apos;incertitude.</p>
        </div>
        <div className="card">
          <h3>Historique complet et auditable</h3>
          <p className="muted">Chaque sélection publiée reste visible, perdante ou gagnante, avec la cote observée au moment de la publication. Aucune réécriture.</p>
        </div>
        <div className="card">
          <h3>Le live, côté serveur</h3>
          <p className="muted">Les matchs sont surveillés en continu. Si le flux de données est en retard, les recommandations sont bloquées.</p>
        </div>
      </div>
    </>
  );
}
