// Ordpresse Lyn-Henter (Userscript / Bookmarklet worker)
(async function() {
  const m = location.hash.match(/ordpresse_id=([^&]+)/);
  const o = location.hash.match(/ordpresse_origin=([^&]+)/);
  const l = location.hash.match(/limit=(\d+)/);
  const bid = m ? m[1] : prompt("Indtast Ordpresse Build-ID:");
  const host = o ? decodeURIComponent(o[1]) : (window.location.origin || "https://ordpresse.seame.click");
  if (!bid) return;
  if (!location.hostname.includes("ordnet.dk")) {
    location.href = "https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id=" + bid + "&ordpresse_origin=" + encodeURIComponent(location.origin) + (l ? "&limit=" + l[1] : "");
    return;
  }
  const box = document.createElement("div");
  box.style = "position:fixed;bottom:20px;right:20px;z-index:999999;background:#111827;color:#fef3c7;padding:16px 20px;border:2px solid #f59e0b;border-radius:12px;font-family:sans-serif;box-shadow:0 10px 25px rgba(0,0,0,0.5);min-width:300px;max-width:400px;";
  document.body.appendChild(box);

  let stopped = false;
  const stopBtn = document.createElement("button");
  stopBtn.innerText = "⏹ Stop & afslut ordbog nu";
  stopBtn.style = "margin-top:10px;padding:6px 12px;background:#b91c1c;color:#fff;border:none;border-radius:6px;font-weight:bold;cursor:pointer;font-size:12px;display:block;width:100%;";
  stopBtn.onclick = async () => {
    stopped = true;
    box.innerHTML = "<b>⏹ Stopper...</b> Sender afslutning til serveren...";
    try {
      await fetch(host + "/api/build/" + bid + "/skip-queue", { method: "POST" });
    } catch(e) {}
    box.innerHTML = "<b>✅ Stoppet!</b> Ordbogen færdiggøres nu med de hentede ord.<br>Du kan lukke denne fane.";
  };

  const url = host + "/api/build/" + bid + "/missing-words" + (l ? "?limit=" + l[1] : "");
  const res = await fetch(url);
  const data = await res.json();
  const words = data.words || [];

  if (words.length === 0) {
    box.innerHTML = "<b>✅ Ingen ord mangler!</b> Alle ord er allerede fundet.";
    return;
  }

  const contentDiv = document.createElement("div");
  box.appendChild(contentDiv);
  box.appendChild(stopBtn);

  for (let i = 0; i < words.length; i++) {
    if (stopped) break;
    const w = words[i];
    contentDiv.innerHTML = "<b>⚡ Ordpresse Lyn-Henter</b><br>Henter <b>" + (i+1) + " / " + words.length + "</b>: <code style='color:#f59e0b;font-size:14px;'>" + w + "</code>";
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

  if (!stopped) {
    box.innerHTML = "<b>✅ Færdig!</b> Alle " + words.length + " ord er sendt til Ordpresse.<br>Serveren kompilerer nu din ordbog færdig.";
  }
})();
