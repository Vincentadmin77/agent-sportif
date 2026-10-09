import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "SportAI · Analyse sportive",
  description: "Analyses football fondées sur un moteur statistique transparent. Aucun résultat n'est garanti.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body>
        <header className="site">
          <div className="wrap">
            <Link href="/" className="brand">SportAI</Link>
            <nav className="main">
              <Link href="/analyses">Analyses</Link>
              <Link href="/methodologie">Méthodologie</Link>
              <Link href="/performances">Performances</Link>
              <Link href="/tarifs">Tarifs</Link>
              <Link href="/faq">FAQ</Link>
            </nav>
            <span className="spacer" />
            <nav className="main">
              <Link href="/espace">Mon espace</Link>
              <Link href="/connexion">Connexion</Link>
            </nav>
          </div>
        </header>
        <main>
          <div className="wrap">{children}</div>
        </main>
        <footer className="site">
          <div className="wrap">
            <p>
              <strong>Jouer comporte des risques : endettement, isolement, dépendance. Pour être aidé, appelez le 09 74 75 13 13 (appel non surtaxé).</strong>{" "}
              Interdit aux moins de 18 ans. Aucune analyse ne garantit un résultat ; les performances passées ne préjugent pas des résultats futurs.
            </p>
            <div className="cols">
              <div>
                <Link href="/plateforme">La plateforme</Link>
                <Link href="/sports">Sports</Link>
                <Link href="/intelligence-artificielle">Fonctionnement de l&apos;IA</Link>
              </div>
              <div>
                <Link href="/risques">Risques liés aux paris</Link>
                <Link href="/contact">Contact</Link>
              </div>
              <div>
                <Link href="/mentions-legales">Mentions légales</Link>
                <Link href="/conditions-generales">Conditions générales</Link>
                <Link href="/confidentialite">Confidentialité</Link>
              </div>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
