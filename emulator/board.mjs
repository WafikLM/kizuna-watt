// Carte capteur VIRTUELLE : execute le .hex de l'Arduino Uno (ATmega328P, 16 MHz) dans l'emulateur avr8js.
// Les deux potentiometres sont remplaces par deux curseurs a l'ecran ; la LED de la broche 13 et la sortie serie sont affichees.
// Chaque ligne JSON envoyee par le programme sur le port serie est transmise a l'application (POST /api/ingest).
//
//   node board.mjs --hex ../proteus/kizuna_sensor/kizuna_sensor.ino.hex --url http://127.0.0.1:8000 --id KW-XXXXXX --key VOTRE_CLE
//   puis ouvrir http://127.0.0.1:8040
import fs from "node:fs";
import http from "node:http";
import { CPU, avrInstruction, AVRTimer, timer0Config, timer1Config, timer2Config, AVRIOPort, portBConfig, portCConfig, portDConfig, AVRUSART, usart0Config, AVRADC, adcConfig, PinState } from "avr8js";

// ---------- arguments ----------
const args = Object.fromEntries(process.argv.slice(2).map((a, i, all) => (a.startsWith("--") ? [a.slice(2), all[i + 1]] : null)).filter(Boolean));
const HEX = args.hex || "../proteus/kizuna_sensor/kizuna_sensor.ino.hex";
const URL = (args.url || "http://127.0.0.1:8000").replace(/\/$/, "");
const DEV_ID = args.id || process.env.KW_ID, DEV_KEY = args.key || process.env.KW_KEY;
const UI_PORT = Number(args["ui-port"] || 8040);
if (!DEV_ID || !DEV_KEY) { console.error("Indiquez --id et --key (ou KW_ID / KW_KEY)."); process.exit(1); }

// ---------- chargement du .hex (format Intel HEX) ----------
function loadHex(path) {
  const bytes = new Uint8Array(0x8000);
  for (const raw of fs.readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line.startsWith(":")) continue;
    const len = parseInt(line.slice(1, 3), 16), addr = parseInt(line.slice(3, 7), 16), type = parseInt(line.slice(7, 9), 16);
    if (type === 1) break;
    if (type !== 0) continue;
    for (let i = 0; i < len; i++) bytes[addr + i] = parseInt(line.slice(9 + i * 2, 11 + i * 2), 16);
  }
  return new Uint16Array(bytes.buffer);
}

// ---------- microcontroleur ----------
const cpu = new CPU(loadHex(HEX));
new AVRTimer(cpu, timer0Config); new AVRTimer(cpu, timer1Config); new AVRTimer(cpu, timer2Config);
const portB = new AVRIOPort(cpu, portBConfig); new AVRIOPort(cpu, portCConfig); new AVRIOPort(cpu, portDConfig);
const usart = new AVRUSART(cpu, usart0Config, 16e6);
const adc = new AVRADC(cpu, adcConfig);

const state = { a0: 0.0, a1: 0.0, led: false, lines: [], sent: 0, last: null, errors: 0 };
const syncAdc = () => { adc.channelValues[0] = state.a0; adc.channelValues[1] = state.a1; };
syncAdc();
portB.addListener(() => { state.led = portB.pinState(5) === PinState.High; });   // broche 13 = PB5

// ---------- sortie serie -> application ----------
let buf = "", lastSend = 0;
async function forward(line) {
  if (Date.now() - lastSend < 1050) return;            // le serveur accepte 1 mesure par seconde
  lastSend = Date.now();
  let obj; try { obj = JSON.parse(line); } catch { return; }
  try {
    const r = await fetch(URL + "/api/ingest", { method: "POST", headers: { "Content-Type": "application/json", "X-Device-Id": DEV_ID, "X-Device-Key": DEV_KEY }, body: JSON.stringify(obj) });
    const msg = r.ok ? "" : (await r.text()).slice(0, 120);
    state.last = { ok: r.ok, code: r.status, at: new Date().toLocaleTimeString("fr-FR"), msg };
    if (r.ok) state.sent++; else state.errors++;
    console.log(state.last.at, r.status, line, msg);
  } catch (e) {
    state.last = { ok: false, code: 0, at: new Date().toLocaleTimeString("fr-FR"), msg: "application injoignable : " + e.message }; state.errors++;
    console.log(state.last.at, "ERREUR", e.message);
  }
}
usart.onByteTransmit = (b) => {
  const c = String.fromCharCode(b);
  if (c === "\n") { const l = buf.trim(); buf = ""; if (l) { state.lines.push(l); if (state.lines.length > 25) state.lines.shift(); if (l.startsWith("{")) forward(l); } }
  else buf += c;
};

// ---------- boucle d'emulation en temps reel ----------
const t0 = Date.now();
setInterval(() => {
  const target = (Date.now() - t0) * 16000;             // 16 MHz = 16000 cycles par milliseconde
  const end = Math.min(target, cpu.cycles + 600000);
  while (cpu.cycles < end) { avrInstruction(cpu); cpu.tick(); }
}, 8);

// ---------- interface locale ----------
const PAGE = `<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>Carte capteur virtuelle</title>
<style>body{margin:0;background:#0b1424;color:#eaf0f8;font:15px system-ui,sans-serif;padding:24px}h1{font-size:20px;margin:0 0 4px}.sub{color:#8fa1b8;font-size:13px}
.card{background:#121d31;border:1px solid #243049;border-radius:14px;padding:18px;margin-top:16px;max-width:760px}input[type=range]{width:100%;accent-color:#ffb42e}
.row{display:flex;justify-content:space-between;align-items:baseline}b{font-size:22px}#led{width:26px;height:26px;border-radius:50%;background:#3a1010;display:inline-block;vertical-align:middle;box-shadow:inset 0 0 6px #000}
#led.on{background:#ff3b3b;box-shadow:0 0 18px #ff3b3b}pre{background:#05090f;color:#8dffc2;padding:12px;border-radius:10px;height:230px;overflow:auto;font:12.5px ui-monospace,Consolas,monospace;margin:8px 0 0}
.ok{color:#34d399}.bad{color:#ff8a92}</style></head><body>
<h1>Carte capteur virtuelle <span class="sub">— ATmega328P / Arduino Uno, 16 MHz</span></h1>
<div class="sub">Le programme de la carte (kizuna_sensor.ino.hex) s'execute ici. Les curseurs remplacent les potentiometres RV1 (A0) et RV2 (A1).</div>
<div class="card"><div class="row"><span>RV1 — courant mesure par la pince (A0)</span><b id="v0"></b></div><input id="a0" type="range" min="0" max="5" step="0.01" value="0">
<div class="row" style="margin-top:14px"><span>RV2 — courant de fuite (A1)</span><b id="v1"></b></div><input id="a1" type="range" min="0" max="5" step="0.01" value="0">
<div style="margin-top:16px">LED d'alerte (broche 13) : <span id="led"></span> <span class="sub">s'allume si fuite ≥ 30 mA ou courant &gt; 80 % du disjoncteur</span></div></div>
<div class="card"><div class="row"><span>Sortie serie (Virtual Terminal, 9600 bauds)</span><span id="st" class="sub"></span></div><pre id="term"></pre></div>
<script>
const $=id=>document.getElementById(id);let t=null;
function show(){$("v0").textContent=(($("a0").value/5)*30).toFixed(1)+" A  ("+Number($("a0").value).toFixed(2)+" V)";$("v1").textContent=(($("a1").value/5)*100).toFixed(0)+" mA  ("+Number($("a1").value).toFixed(2)+" V)";}
function send(){show();clearTimeout(t);t=setTimeout(()=>fetch("/pots",{method:"POST",body:JSON.stringify({a0:+$("a0").value,a1:+$("a1").value})}),60);}
$("a0").oninput=$("a1").oninput=send;show();
async function poll(){try{const s=await(await fetch("/state")).json();$("led").className=s.led?"on":"";const term=$("term"),bottom=term.scrollTop+term.clientHeight>=term.scrollHeight-8;term.textContent=s.lines.join("\\n");if(bottom)term.scrollTop=term.scrollHeight;
$("st").innerHTML=s.last?(s.last.ok?'<span class="ok">envoye a l application : '+s.sent+' mesures (derniere '+s.last.at+')</span>':'<span class="bad">erreur '+s.last.code+' '+s.last.msg+'</span>'):"en attente de la premiere mesure (2 s)...";}catch(e){}setTimeout(poll,500);}poll();
</script></body></html>`;

http.createServer((req, res) => {
  if (req.method === "POST" && req.url === "/pots") {
    let body = ""; req.on("data", (c) => (body += c)); req.on("end", () => {
      try { const p = JSON.parse(body); state.a0 = Math.max(0, Math.min(5, +p.a0 || 0)); state.a1 = Math.max(0, Math.min(5, +p.a1 || 0)); syncAdc(); } catch {}
      res.end("ok");
    }); return;
  }
  if (req.url === "/state") { res.setHeader("Content-Type", "application/json"); return res.end(JSON.stringify(state)); }
  res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(PAGE);
}).listen(UI_PORT, "127.0.0.1", () => console.log(`Carte virtuelle : ouvrez http://127.0.0.1:${UI_PORT}  (mesures envoyees a ${URL})`));
