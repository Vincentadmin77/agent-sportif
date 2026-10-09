export default function Page() {
  return (
    <>
      <h1>Sports</h1>
      <div className="grid">
        <div className="card"><h3>Football <span className="badge ok">disponible</span></h3>
          <p className="muted">Modèle de scores Dixon-Coles, marchés résultat, double chance, remboursé si nul, totaux de buts, les deux équipes marquent. Surveillance live.</p></div>
        <div className="card muted"><h3>Basket-ball <span className="badge">en préparation</span></h3>
          <p className="muted">Modèles fondés sur les possessions, l&apos;efficacité offensive, les rotations et le calendrier. Lancement après validation du moteur football.</p></div>
        <div className="card muted"><h3>Tennis <span className="badge">en préparation</span></h3>
          <p className="muted">Modèles par surface, service et retour, fatigue et format de match.</p></div>
      </div>
    </>
  );
}
