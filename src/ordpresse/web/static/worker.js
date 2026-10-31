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
  const items = data.items || (data.words || []).map(w => ({word: w, target: "ods", priority: 1}));

  if (items.length === 0) {
    box.innerHTML = "<b>✅ Ingen ord mangler!</b> Alle ord er allerede fundet.";
    return;
  }

  const contentDiv = document.createElement("div");
  box.appendChild(contentDiv);
  box.appendChild(stopBtn);

  // Progressive pacing and jitter based on batch size
  let baseDelay = 350;
  let jitterRange = 150;
  if (items.length > 500) {
    baseDelay = 1100;
    jitterRange = 700;
  } else if (items.length > 250) {
    baseDelay = 650;
    jitterRange = 450;
  } else if (items.length > 100) {
    baseDelay = 450;
    jitterRange = 250;
  }

  for (let i = 0; i < items.length; i++) {
    if (stopped) break;
    const it = items[i];
    const w = typeof it === "string" ? it : it.word;
    const target = (it && it.target === "ddo") ? "ddo" : "ods";
    const prioLabel = (it && it.priority === 2) ? " [Prio 2: DDO]" : " [Prio 1: ODS]";
    contentDiv.innerHTML = "<b>⚡ Ordpresse Lyn-Henter</b><br>Henter <b>" + (i+1) + " / " + items.length + "</b>" + prioLabel + ": <code style='color:#f59e0b;font-size:14px;'>" + w + "</code>";

    try {
      const endpoint = target === "ddo" ? "https://ordnet.dk/ddo/ordbog?query=" : "https://ordnet.dk/ods/ordbog?query=";
      const r = await fetch(endpoint + encodeURIComponent(w));

      if (r.status === 429) {
        contentDiv.innerHTML = "<b>⚠️ ordnet.dk bad om en pause (HTTP 429).</b><br>Venter 20 sekunder før automatisk genoptagelse...";
        baseDelay += 400;
        jitterRange += 300;
        await new Promise(res => setTimeout(res, 20000));
        i--;
        continue;
      }

      if (r.ok) {
        const html = await r.text();
        await fetch(host + "/api/cache/submit", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({word: w, html: html, build_id: bid})
        });
      } else {
        console.warn("Uventet HTTP status for " + w + ": " + r.status);
      }
    } catch (e) {
      console.error("Fejl under hentning af " + w + ":", e);
    }

    const progressiveExtra = (i > 500) ? Math.min(500, Math.floor((i - 500) / 100) * 50) : 0;
    const actualDelay = baseDelay + progressiveExtra + Math.floor(Math.random() * jitterRange);
    await new Promise(r => setTimeout(r, actualDelay));
  }

  if (!stopped) {
    try {
      await fetch(host + "/api/build/" + bid + "/skip-queue", { method: "POST" });
    } catch(e) {}
    box.innerHTML = "<b>✅ Færdig!</b> Alle " + items.length + " ord er sendt til Ordpresse.<br>Serveren kompilerer nu din ordbog færdig.";
  }
})();
