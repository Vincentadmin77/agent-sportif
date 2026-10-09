// End-to-end smoke test against a running stack (web on :3000, API behind /api).
// Usage: ADMIN_EMAIL=... ADMIN_PASSWORD=... node e2e/smoke.mjs [screenshot-dir]
import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://localhost:3000";
const out = process.argv[2] ?? ".";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const check = (cond, msg) => { if (!cond) throw new Error(`FAILED: ${msg}`); console.log(`ok - ${msg}`); };

await page.goto(base + "/");
check((await page.textContent("h1")).includes("Moins de sélections"), "home page renders");
await page.screenshot({ path: `${out}/home.png`, fullPage: true });

// A new user sees the teaser only, and no admin.
const email = `user${Date.now()}@example.com`;
await page.goto(base + "/inscription");
await page.fill("input[type=email]", email);
await page.fill("input[type=password]", "user-password-1");
await page.check("input[type=checkbox]");
await page.click("button.btn");
await page.waitForURL("**/espace");
await page.waitForSelector("h1");
check((await page.textContent("h1")).includes("Sélections du moment"), "new user lands on dashboard");
await page.goto(base + "/admin");
await page.waitForSelector("text=Accès réservé aux administrateurs");
check(true, "non-admin is refused the admin panel");
const status = await page.evaluate(async () => (await fetch("/api/admin/runs")).status);
check(status === 403, "admin API refuses a non-admin (server-side)");
await page.goto(base + "/espace/historique");
await page.waitForSelector("text=Réservé aux abonnés");
check(true, "history gated for free plan");

// Admin launches a simulated analysis and sees it flagged as simulation.
await page.evaluate(() => fetch("/api/auth/logout", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }));
await page.goto(base + "/connexion");
await page.fill("input[type=email]", process.env.ADMIN_EMAIL);
await page.fill("input[type=password]", process.env.ADMIN_PASSWORD);
await page.click("button.btn");
await page.waitForURL("**/espace");
await page.goto(base + "/admin");
await page.click("text=Lancer une simulation");
await page.waitForSelector("text=Données simulées");
check(true, "simulation run is labelled and marked non-publishable");
await page.screenshot({ path: `${out}/admin-run.png`, fullPage: true });
await page.click("text=Lancer une analyse réelle");
await page.waitForSelector("text=provider_unavailable");
check(true, "real run without API keys reports provider_unavailable");
await page.goto(base + "/espace");
await page.waitForSelector("text=Aucune sélection publiée");
check(true, "no simulated selection leaks to the subscriber dashboard");
await page.screenshot({ path: `${out}/dashboard.png`, fullPage: true });
await browser.close();
