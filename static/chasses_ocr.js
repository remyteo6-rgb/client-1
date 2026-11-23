/* Lecture d'une capture d'écran du tableau GPS (colonne « CHASSES ≥24 ») dans le navigateur, avec Tesseract.js.
   Rien n'est envoyé à un serveur d'IA : la lecture se fait sur l'ordinateur. Le résultat est montré avant
   enregistrement pour que tu corriges une éventuelle erreur de lecture.
   lireCapture(source, worker, makeCanvas) -> [{nom, pct}] ; source : canvas/image déjà décodée. */
(function (root) {
  var ECHELLE = 3;
  function entree(c) { return c.toBuffer ? c.toBuffer('image/png') : c; }

  function mots(res) {
    return (res.data.words || []).map(function (w) {
      var t = (w.text || '').trim();
      return { t: t, x: w.bbox.x0 / ECHELLE, y: w.bbox.y0 / ECHELLE,
               w: (w.bbox.x1 - w.bbox.x0) / ECHELLE, h: (w.bbox.y1 - w.bbox.y0) / ECHELLE };
    }).filter(function (w) { return w.t; }).map(function (w) {
      w.cx = w.x + w.w / 2; w.cy = w.y + w.h / 2; return w;
    });
  }

  async function lireCapture(source, worker, makeCanvas, progres) {
    var W0 = source.width, H0 = source.height;
    var grand = makeCanvas(W0 * ECHELLE, H0 * ECHELLE);
    var g = grand.getContext('2d');
    g.imageSmoothingQuality = 'high';
    g.drawImage(source, 0, 0, W0 * ECHELLE, H0 * ECHELLE);
    await worker.setParameters({ tessedit_pageseg_mode: '11', tessedit_char_whitelist: '' });
    var W = mots(await worker.recognize(entree(grand)));
    var tetes = W.filter(function (w) { return /^JOUEUR$/i.test(w.t); }).sort(function (a, b) { return a.x - b.x; });
    if (!tetes.length) throw new Error("En-tête « JOUEUR » introuvable : recadre la capture sur le tableau.");
    var tableaux = [];
    tetes.forEach(function (hj, k) {
      var x0 = hj.x - 5, x1 = k + 1 < tetes.length ? tetes[k + 1].x - 5 : 1e9;
      var seg = W.filter(function (w) { return w.x >= x0 && w.x < x1 && Math.abs(w.cy - hj.cy) < 8; });
      var ch = seg.filter(function (w) { return /^CHASS/i.test(w.t); }).sort(function (a, b) { return a.x - b.x; });
      if (ch.length < 2) return;
      var col = ch[1];
      var droite = Math.max.apply(null, seg.filter(function (w) { return w.x > col.x && w.x < col.x + col.w + 40; })
        .map(function (w) { return w.x + w.w; }).concat([col.x + col.w]));
      var rh = seg.filter(function (w) { return /^RHI/i.test(w.t); });
      var nomX1 = (rh.length ? rh[0].x : ch[0].x) - 3;
      var lignes = [];
      W.filter(function (w) { return w.x >= x0 && w.x < nomX1 && w.cy > hj.cy + 10; })
        .sort(function (a, b) { return a.cy - b.cy; }).forEach(function (w) {
          var l = lignes.filter(function (r) { return Math.abs(r.cy - w.cy) < 6; })[0];
          if (l) l.ws.push(w); else lignes.push({ cy: w.cy, ws: [w] });
        });
      // Grille régulière : une ligne mal lue ne doit pas disparaître. Le pas est la médiane des écarts entre lignes lues.
      var cys = lignes.map(function (l) { return l.cy; }).sort(function (a, b) { return a - b; });
      var ecarts = [];
      for (var q = 1; q < cys.length; q++) ecarts.push(cys[q] - cys[q - 1]);
      var pas = ecarts.length ? ecarts.slice().sort(function (a, b) { return a - b; })[Math.floor(ecarts.length / 2)] : 24;
      pas = Math.max(14, pas > 40 ? pas / Math.round(pas / 24) : pas);
      var grille = [];
      if (cys.length) {
        var y = cys[0];
        while (y - pas > hj.cy + 12) y -= pas;
        for (; y <= cys[cys.length - 1] + pas * 0.5; y += pas) {
          var proche = cys.filter(function (c) { return Math.abs(c - y) < pas * 0.4; })[0];
          grille.push({ cy: proche !== undefined ? proche : y });
        }
      }
      lignes = grille;
      var nomX0 = hj.x - 4;      tableaux.push({ cx: (col.x + droite) / 2, lignes: lignes, nomX0: nomX0, nomX1: nomX1, pas: pas });
    });
    if (!tableaux.length) throw new Error("Colonne « CHASSES ≥24 » introuvable : la capture doit montrer les en-têtes du tableau.");
    await worker.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789-' });
    var out = [], total = tableaux.reduce(function (s, t) { return s + t.lignes.length; }, 0), fait = 0;
    for (var ti = 0; ti < tableaux.length; ti++) {
      var tb = tableaux[ti];
      for (var li = 0; li < tb.lignes.length; li++) {
        var l = tb.lignes[li];
        fait++; if (progres) progres(fait, total);
        // Nom : relu sur la ligne entière (plus fiable que les mots isolés de la lecture globale).
        var nw = Math.max(20, tb.nomX1 - tb.nomX0), nh = Math.round(tb.pas * 0.85), nc = makeCanvas(nw * 4, nh * 4), ng = nc.getContext('2d');
        ng.fillStyle = '#fff'; ng.fillRect(0, 0, nw * 4, nh * 4);
        ng.drawImage(source, tb.nomX0, l.cy - nh / 2, nw, nh, 0, 0, nw * 4, nh * 4);
        await worker.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '' });
        var rn = await worker.recognize(entree(nc));
        var nom = (rn.data.text || '').replace(/[|\[\]_]/g, ' ').replace(/\s+/g, ' ').replace(/[.…]+\s*$/, '').trim();
        if (!/[A-Za-zÀ-ÿ]{3}/.test(nom) || /^(moyenne|m[ée]diane)/i.test(nom)) continue;
        await worker.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789-' });
        var cw = 64, chh = 18, cell = makeCanvas(cw * 5, chh * 5), cg = cell.getContext('2d');
        cg.fillStyle = '#fff'; cg.fillRect(0, 0, cw * 5, chh * 5);
        cg.drawImage(source, tb.cx - cw / 2, l.cy - chh / 2, cw, chh, 0, 0, cw * 5, chh * 5);
        var im = cg.getImageData(0, 0, cw * 5, chh * 5), d = im.data;
        for (var i = 0; i < d.length; i += 4) {
          var v = (d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114) < 110 ? 0 : 255;
          d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255;
        }
        cg.putImageData(im, 0, 0);
        var r = await worker.recognize(entree(cell));
        var m = /\d{1,3}/.exec((r.data.text || '').trim());
        out.push({ nom: nom, pct: m ? Math.min(100, parseInt(m[0], 10)) : null });
      }
    }
    return out;
  }
  root.lireCapture = lireCapture;
})(typeof window !== 'undefined' ? window : globalThis);
