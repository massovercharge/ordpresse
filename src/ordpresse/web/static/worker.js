// Ordpresse Lyn-Henter (Userscript / Bookmarklet worker)
(async function() {
  const m = location.hash.match(/ordpresse_id=([^&]+)/);
  const o = location.hash.match(/ordpresse_origin=([^&]+)/);
  const bid = m ? m[1] : prompt("Indtast Ordpresse Build-ID:");
  const host = o ? decodeURIComponent(o[1]) : window.location.origin;
  if (!bid) return;
  if (!location.hostname.includes("ordnet.dk")) {
    location.href = "https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id=" + bid + "&ordpresse_origin=" + encodeURIComponent(location.origin);
    return;
  }
  const box = document.createElement("div");
  box.style = "position:fixed;bottom:20px;right:20px;z-index:999999;background:#111827;color:#fef3c7;padding:16px 20px;border:2px solid #f59e0b;border-radius:10px;font-family:sans-serif;box-shadow:0 10px 25px rgba(0,0,0,0.5);min-width:280px;";
  document.body.appendChild(box);
  const res = await fetch(host + "/api/build/" + bid + "/missing-words");
  const data = await res.json();
  const words = data.words || [];
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    box.innerHTML = "<b>⚡ Ordpresse Lyn-Henter</b><br>Henter " + (i+1) + " / " + words.length + ": <b>" + w + "</b>";
    try {
      const r = await fetch("https://ordnet.dk/ods/ordbog?query=" + encodeURIComponent(w));
      const html = await r.text();
      await fetch(host + "/api/cache/submit", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({word: w, html: html, build_id: bid})
      });
    } catch (e) { console.error(e); }
    await new Promise(r => setTimeout(r, 350));
  }
  box.innerHTML = "<b>✅ Færdig!</b> Alle " + words.length + " ord er sendt til Ordpresse.<br>Du kan lukke denne fane nu.";
})();
