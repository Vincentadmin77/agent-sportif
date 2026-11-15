/* Analytrack autonome : remplace window.claude (base de l'artifact Claude) par les fichiers publiés par le serveur.
 *
 * La page index.html reste celle de l'artifact, mot pour mot : elle appelle window.claude.use("db"), ("user"),
 * ("downloads"), ("mcp"). Ici :
 *  - db : lecture de data/<collection>.json (écrits par autonome/etapes.py publier), relue toutes les 60 s ;
 *         onSnapshot rappelle la page à chaque changement, comme la base Claude ;
 *         écritures : carnet personnel et prévente gardés dans le navigateur (localStorage) et envoyés au serveur
 *         sur /api/ecrire s'il existe (à brancher avec la gestion des abonnés) ; jamais d'écriture des données
 *         d'analyse depuis le site.
 *  - user : visiteur anonyme (identifiant tiré au hasard, gardé dans le navigateur), jamais administrateur ;
 *  - downloads : téléchargement par le navigateur ; mcp : absent (pas de bouton « analyse à la demande »).
 */
(function () {
  "use strict";
  var BASE = (window.ANALYTRACK_DATA || "data/");
  var PERIODE = 60000;
  var cache = {};      // collection -> {docs: {id: data}, texte: string}
  var abonnes = {};    // collection -> [fonction]
  var enCours = {};

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  function charger(coll) {
    if (enCours[coll]) return enCours[coll];
    // données propres au visiteur : seulement dans son navigateur (jamais publiées par le serveur)
    var aDistance = !/^(carnet_compte|prevente|data\/users\/.*)$/.test(coll);
    enCours[coll] = (aDistance ? fetch(BASE + coll + ".json?t=" + Math.floor(Date.now() / PERIODE), { cache: "no-cache" })
      .then(function (r) { return r.ok ? r.text() : "{}"; })
      .catch(function () { return null; }) : Promise.resolve("{}"))
      .then(function (texte) {
        enCours[coll] = null;
        if (texte === null) return cache[coll] || { docs: {}, texte: "" };
        var ancien = cache[coll];
        if (ancien && ancien.texte === texte) return ancien;
        var docs = {};
        try { docs = JSON.parse(texte) || {}; } catch (e) { docs = {}; }
        // documents gardés dans ce navigateur (carnet perso, prévente)
        var loc = JSON.parse(lsGet("analytrack-db-" + coll) || "{}");
        Object.keys(loc).forEach(function (id) { docs[id] = loc[id]; });
        cache[coll] = { docs: docs, texte: texte };
        (abonnes[coll] || []).forEach(function (f) { try { f(); } catch (e) {} });
        return cache[coll];
      });
    return enCours[coll];
  }

  setInterval(function () { Object.keys(abonnes).forEach(charger); }, PERIODE);

  function docSnap(id, data) {
    return { id: id, exists: data !== undefined && data !== null, data: function () { return data == null ? null : JSON.parse(JSON.stringify(data)); } };
  }

  function requete(coll, opts) {
    opts = opts || {};
    function liste() {
      var c = cache[coll];
      var ids = c ? Object.keys(c.docs) : [];
      var docs = ids.map(function (id) { return { id: id, d: c.docs[id] }; });
      (opts.where || []).forEach(function (w) {
        docs = docs.filter(function (x) {
          var v = x.d && x.d[w[0]];
          switch (w[1]) {
            case "==": case "eq": return v === w[2];
            case "!=": case "ne": return v !== w[2];
            case ">": case "gt": return v > w[2];
            case ">=": case "gte": return v >= w[2];
            case "<": case "lt": return v < w[2];
            case "<=": case "lte": return v <= w[2];
            case "in": return (w[2] || []).indexOf(v) >= 0;
            case "array-contains": return Array.isArray(v) && v.indexOf(w[2]) >= 0;
            default: return true;
          }
        });
      });
      if (opts.orderBy) {
        var f = opts.orderBy[0], sens = opts.orderBy[1] === "desc" ? -1 : 1;
        docs.sort(function (a, b) {
          var x = a.d && a.d[f], y = b.d && b.d[f];
          return (x === y ? 0 : (x > y ? 1 : -1)) * sens;
        });
      } else {
        docs.sort(function (a, b) { return a.id < b.id ? -1 : a.id > b.id ? 1 : 0; });
      }
      if (opts.limit) docs = docs.slice(0, opts.limit);
      return { docs: docs.map(function (x) { return docSnap(x.id, x.d); }), size: docs.length, empty: !docs.length };
    }
    var q = {
      limit: function (n) { return requete(coll, Object.assign({}, opts, { limit: n })); },
      orderBy: function (f, sens) { return requete(coll, Object.assign({}, opts, { orderBy: [f, sens || "asc"] })); },
      where: function (f, op, v) { return requete(coll, Object.assign({}, opts, { where: (opts.where || []).concat([[f, op, v]]) })); },
      get: function () { return charger(coll).then(liste); },
      onSnapshot: function (cb, err) {
        var f = function () { cb(liste()); };
        (abonnes[coll] = abonnes[coll] || []).push(f);
        charger(coll).then(function () { if (!cache[coll] || !cache[coll].texte) f(); }, err || function () {});
        if (cache[coll]) setTimeout(f, 0);
        return function () { abonnes[coll] = (abonnes[coll] || []).filter(function (g) { return g !== f; }); };
      },
      doc: function (id) { return refDoc(coll, id); }
    };
    return q;
  }

  function refDoc(coll, id) {
    function lire() { var c = cache[coll]; return docSnap(id, c ? c.docs[id] : undefined); }
    return {
      id: id,
      get: function () { return charger(coll).then(lire); },
      onSnapshot: function (cb, err) {
        var f = function () { cb(lire()); };
        (abonnes[coll] = abonnes[coll] || []).push(f);
        charger(coll).then(function () { if (!cache[coll] || !cache[coll].texte) f(); }, err || function () {});
        if (cache[coll]) setTimeout(f, 0);
        return function () { abonnes[coll] = (abonnes[coll] || []).filter(function (g) { return g !== f; }); };
      },
      set: function (data) { return ecrire(coll, id, data, false); },
      update: function (data) { return ecrire(coll, id, data, true); },
      delete: function () { return ecrire(coll, id, null, false); }
    };
  }

  // Seules les données d'un visiteur peuvent être écrites depuis le site (carnet, prévente). Le reste est refusé.
  var ECRITURE_VISITEUR = /^(carnet_compte|prevente|data\/users\/[^/]+)$/;

  function ecrire(coll, id, data, fusion) {
    if (!ECRITURE_VISITEUR.test(coll)) return Promise.reject(new Error("lecture seule"));
    var cle = "analytrack-db-" + coll;
    var loc = JSON.parse(lsGet(cle) || "{}");
    if (data === null) delete loc[id];
    else loc[id] = fusion ? Object.assign({}, loc[id] || {}, data) : data;
    lsSet(cle, JSON.stringify(loc));
    if (cache[coll]) {
      if (data === null) delete cache[coll].docs[id]; else cache[coll].docs[id] = loc[id];
      (abonnes[coll] || []).forEach(function (f) { try { f(); } catch (e) {} });
    }
    // Serveur (gestion des abonnés, à venir) : silencieux tant que /api/ecrire n'existe pas.
    fetch("/api/ecrire", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ collection: coll, doc_id: id, data: data, fusion: !!fusion }) }).catch(function () {});
    return Promise.resolve();
  }

  function base() {
    return {
      collection: function (p) {
        var s = String(p).split("/");
        if (s.length % 2 === 0) return refDoc(s.slice(0, -1).join("/"), s[s.length - 1]);
        return requete(p);
      },
      doc: function (p) { var s = String(p).split("/"); return refDoc(s.slice(0, -1).join("/"), s[s.length - 1]); }
    };
  }

  function utilisateur() {
    var id = lsGet("analytrack-visiteur");
    if (!id) { id = "v_" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36); lsSet("analytrack-visiteur", id); }
    return { id: function () { return Promise.resolve(id); }, isOwner: function () { return Promise.resolve(false); },
      name: function () { return Promise.resolve(null); } };
  }

  function telechargements() {
    return {
      save: function (nom, contenu, type) {
        if (nom && typeof nom === "object") { contenu = nom.data; type = nom.mimeType || nom.type; nom = nom.filename; }
        var b = contenu instanceof Blob ? contenu : new Blob([contenu], { type: type || "application/octet-stream" });
        var a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = nom || "analytrack";
        document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        return Promise.resolve();
      }
    };
  }
  var tel = telechargements();
  tel.download = tel.save;

  window.claude = {
    autonome: true,
    use: function (nom) {
      if (nom === "db") return Promise.resolve(base());
      if (nom === "user") return Promise.resolve(utilisateur());
      if (nom === "downloads") return Promise.resolve(tel);
      return Promise.reject(new Error(nom + " indisponible hors de Claude"));
    }
  };
})();
