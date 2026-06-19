"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Scissors, Palette, Sparkles,
  ArrowLeft, Play, SkipBack, SkipForward,
  Volume2, Maximize2, Wand2, Check, Loader2,
  Send, BrainCircuit, Layers, ZoomIn,
  Film, SlidersHorizontal,
  Music2, Expand,
} from "lucide-react";
import Link from "next/link";
import { Dock } from "@/components/Dock";
import { AppSidebar } from "@/components/AppSidebar";

/* ─── Helpers ────────────────────────────────────────────── */
const B = (o = 0.09) => `rgba(255,255,255,${o})`;
const S = (o = 0.04) => `rgba(255,255,255,${o})`;

const TABS = [
  { id: "montage",    label: "Schnitt",         icon: Scissors, color: "var(--cut)",   hex: "#ff7846" },
  { id: "etalonnage", label: "Farbkorrektur",   icon: Palette,  color: "var(--grade)", hex: "#c084fc" },
  { id: "effets",     label: "Effekte",         icon: Sparkles, color: "var(--fx)",    hex: "#34d399" },
];

const CLIPS = [
  { id:"c1", name:"Außen Nacht — Eingang",  dur:"0:08", take:3, score:94, sel:true  },
  { id:"c2", name:"Blick — Fenster",         dur:"0:04", take:1, score:88, sel:true  },
  { id:"c3", name:"Gang Flur",               dur:"0:12", take:5, score:71, sel:false },
  { id:"c4", name:"Nahaufnahme — Hände",    dur:"0:06", take:2, score:92, sel:true  },
  { id:"c5", name:"Gegenlicht",              dur:"0:09", take:4, score:67, sel:false },
  { id:"c6", name:"Abschlussschwenk",        dur:"0:15", take:1, score:97, sel:true  },
];

const MSGS = [
  { r:"ai",   t:"Ich habe Ihre 6 Clips analysiert. 4 Takes ausgewählt (Score >85). Der vorgeschlagene Rough Cut dauert 39s — Rhythmus passend zum dramatischen Register." },
  { r:"user", t:"Starte Farbkorrektur Szene 1, Stil ‘Wong Kar-wai Atmosphäre, tiefe Blautöne’." },
  { r:"ai",   t:"LUT-Generierung — Temperatur 4200K, Blau-Boost Schatten +18, Grün-Entsättigung, 35mm-Filmkorn. Ergebnis in ~12s." },
];

function scoreC(n: number) {
  return n >= 90 ? "var(--fx)" : n >= 75 ? "var(--gold)" : "var(--t3)";
}

/* ══════════════════════════════════════════════════════════
   MONTAGE
═════════════════════════════════════════════════════════ */
function MontageTab() {
  const [sel, setSel] = useState(CLIPS.filter(c=>c.sel).map(c=>c.id));

  return (
    <div style={{ display:"flex", gap:12, height:"100%", minHeight:0 }}>
      {/* Clip list */}
      <div style={{ width:242, flexShrink:0, display:"flex", flexDirection:"column", gap:4, overflowY:"auto" }}>
        <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:4 }}>
          <span className="label">CLIPS — {CLIPS.length}</span>
          <button style={{
            fontSize:10, fontWeight:600, padding:"2px 8px", borderRadius:6,
            background:"var(--gold-s)", border:"1px solid var(--gold-b)",
                color:"var(--cut)", cursor:"pointer",
          }}>KI sortieren</button>
        </div>
        {CLIPS.map(clip => {
          const active = sel.includes(clip.id);
          return (
            <motion.div
              key={clip.id}
              onClick={() => setSel(p => active ? p.filter(id=>id!==clip.id) : [...p,clip.id])}
              style={{
                display:"flex", alignItems:"center", gap:8, padding:"8px 10px", borderRadius:10,
                background: active ? "var(--cut-s)" : S(0.025),
                border: `1px solid ${active ? "rgba(255,120,70,.25)" : B(0.06)}`,
                cursor:"pointer",
              }}
              whileHover={{ borderColor: B(0.14) }}
              whileTap={{ scale:0.98 }}
            >
              <div style={{
                width:44, height:28, borderRadius:6,
                background:"linear-gradient(135deg,#060e1f,#0d2252)",
                border:`1px solid ${B(0.07)}`,
                display:"flex", alignItems:"center", justifyContent:"center",
                flexShrink:0,
              }}>
                <Film size={10} style={{ color:"var(--t4)" }} />
              </div>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontSize:12, fontWeight:500, color:"var(--t0)", letterSpacing:"-0.01em", marginBottom:2 }} className="truncate">
                  {clip.name}
                </div>
                <div style={{ display:"flex", gap:6, fontSize:10, color:"var(--t3)", fontFamily:"var(--mono)" }}>
                  <span>{clip.dur}</span><span>·</span><span>P{clip.take}</span>
                </div>
              </div>
              <span style={{ fontSize:11, fontWeight:700, color:scoreC(clip.score), fontFamily:"var(--mono)", flexShrink:0 }}>
                {clip.score}
              </span>
            </motion.div>
          );
        })}
      </div>

      {/* Right: jobs + timeline */}
      <div style={{ flex:1, display:"flex", flexDirection:"column", gap:10, minWidth:0, minHeight:0 }}>
        {/* Jobs */}
        <div style={{ padding:12, borderRadius:12, background:S(0.03), border:`1px solid ${B(0.06)}` }}>
          <div style={{ display:"flex", alignItems:"center", gap:6, marginBottom:10 }}>
            <Wand2 size={12} style={{ color:"var(--gold)" }} />
            <span style={{ fontSize:12, fontWeight:500, color:"var(--t1)" }}>KI-Analyse</span>
          </div>
          <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr 1fr", gap:6 }}>
            {[
              { name:"Schärfeanalyse",        st:"done"    },
              { name:"Auto Rough Cut",         st:"done"    },
              { name:"Rhythmus-Vorschläge",    st:"running" },
              { name:"Narrative Kohärenz",     st:"pending" },
            ].map(j => (
              <div key={j.name} style={{
                display:"flex", alignItems:"center", gap:6, padding:"7px 10px",
                borderRadius:9, background:S(0.025), border:`1px solid ${B(0.06)}`,
              }}>
                {j.st==="done" ? <Check size={11} style={{ color:"var(--fx)",flexShrink:0 }} />
                 : j.st==="running" ? (
                   <motion.div animate={{ rotate:360 }} transition={{ duration:1.2, repeat:Infinity, ease:"linear" }}>
                     <Loader2 size={11} style={{ color:"var(--gold)",flexShrink:0 }} />
                   </motion.div>
                 ) : <div style={{ width:11,height:11,borderRadius:"50%",background:B(0.1),flexShrink:0 }} />}
                <span style={{ fontSize:11, color:"var(--t2)" }} className="truncate">{j.name}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Timeline */}
        <div style={{ flex:1, padding:14, borderRadius:12, background:S(0.03), border:`1px solid ${B(0.06)}`, minHeight:0 }}>
          <div style={{ display:"flex", justifyContent:"space-between", marginBottom:12 }}>
            <span className="label">TIMELINE — ROUGH CUT</span>
            <span className="mono" style={{ color:"var(--t3)", fontSize:11 }}>00:00:39:00</span>
          </div>
          {["Video","Audio","Musik"].map((track, ti) => (
            <div key={track} style={{ display:"flex", alignItems:"center", gap:10, marginBottom:8 }}>
              <span className="label" style={{ width:40, textAlign:"right", flexShrink:0 }}>{track}</span>
              <div style={{
                flex:1, display:"flex", gap:1, borderRadius:8, overflow:"hidden",
                height: ti===0 ? 36 : 20,
                background:S(0.03), border:`1px solid ${B(0.05)}`,
              }}>
                {CLIPS.filter(c=>sel.includes(c.id)).map((clip, ci) => (
                  <motion.div key={clip.id}
                    style={{
                      flex: parseInt(clip.dur.split(":")[1]||"5"),
                      minWidth:20,
                      display:"flex", alignItems:"center", paddingLeft:6,
                      background: ti===0 ? "linear-gradient(90deg,rgba(255,120,70,.35),rgba(255,120,70,.18))"
                                : ti===1 ? "rgba(91,156,246,.22)" : "rgba(192,132,252,.22)",
                      borderRight:`1px solid ${B(0.06)}`,
                      cursor:"pointer",
                    }}
                    initial={{ scaleX:0, originX:0 }}
                    animate={{ scaleX:1 }}
                    transition={{ duration:0.25, delay:ci*0.04+0.1 }}
                    whileHover={{ opacity:0.75 }}
                  >
                    {ti===0 && <span style={{ fontSize:9, color:"rgba(255,255,255,.4)", fontFamily:"var(--mono)" }}>P{ci+1}</span>}
                  </motion.div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════
   ÉTALONNAGE
═════════════════════════════════════════════════════════ */
function EtalonnageTab() {
  const [prompt, setPrompt] = useState("film noir années 40, contraste élevé, grain 35mm, ombres bleues");

  const scenes = [
    { id:"s1", name:"Ext. nuit",        bg:"#0d2252", temp:4200, tint:-8 },
    { id:"s2", name:"Int. appartement", bg:"#3d1a08", temp:3800, tint:4  },
    { id:"s3", name:"Couloir",          bg:"#151528", temp:5000, tint:-2  },
    { id:"s4", name:"Contre-jour",      bg:"#2a180a", temp:6500, tint:12  },
  ];

  return (
    <div style={{ display:"flex", gap:12, height:"100%", minHeight:0 }}>
      {/* Scene list */}
      <div style={{ width:200, flexShrink:0, overflowY:"auto", display:"flex", flexDirection:"column", gap:4 }}>
        <span className="label" style={{ marginBottom:4, display:"block" }}>SZENEN</span>
        {scenes.map(sc => (
          <motion.div key={sc.id} style={{
            padding:10, borderRadius:10,
            background:S(0.03), border:`1px solid ${B(0.06)}`, cursor:"pointer",
          }}
          whileHover={{ borderColor:"rgba(192,132,252,.3)" }}>
            <div style={{ width:"100%", height:40, borderRadius:7, background:sc.bg, marginBottom:8, border:`1px solid ${B(0.07)}` }} />
            <div style={{ fontSize:12, fontWeight:500, color:"var(--t0)", marginBottom:3, letterSpacing:"-0.01em" }}>{sc.name}</div>
            <div style={{ display:"flex", gap:6, fontSize:10, color:"var(--t3)", fontFamily:"var(--mono)" }}>
              <span>{sc.temp}K</span><span>·</span><span>Tint {sc.tint>0?"+":""}{sc.tint}</span>
            </div>
          </motion.div>
        ))}
      </div>

      {/* Controls */}
      <div style={{ flex:1, display:"flex", flexDirection:"column", gap:10, minWidth:0 }}>
        {/* LUT prompt */}
        <div style={{ padding:14, borderRadius:12, background:S(0.03), border:`1px solid ${B(0.06)}` }}>
          <div style={{ display:"flex", alignItems:"center", gap:6, marginBottom:10 }}>
            <Wand2 size={12} style={{ color:"var(--grade)" }} />
            <span style={{ fontSize:12, fontWeight:500, color:"var(--t1)" }}>LUT per Prompt generieren</span>
          </div>
          <div style={{ display:"flex", alignItems:"center", gap:8, background:"var(--bg-raised)", border:"1px solid var(--b1)", borderRadius:9, padding:"6px 10px", marginBottom:10 }}>
            <input value={prompt} onChange={e=>setPrompt(e.target.value)}
              style={{ flex:1, background:"none", border:"none", outline:"none", color:"var(--t0)", fontSize:12, caretColor:"var(--grade)" }} />
            <motion.button
              style={{ fontSize:11, fontWeight:600, padding:"4px 12px", borderRadius:7,
                background:"var(--grade-s)", border:"1px solid rgba(192,132,252,.3)",
                color:"var(--grade)", cursor:"pointer", whiteSpace:"nowrap" }}
              whileHover={{ background:"rgba(192,132,252,.22)" }} whileTap={{ scale:0.95 }}
            >Generieren</motion.button>
          </div>
          <div style={{ display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:8 }}>
            {["Blauer Dunst","Urbane Nacht","Kontrast +","Sanftes Amber"].map((lut,i) => (
              <motion.div key={lut} style={{
                padding:8, borderRadius:9, cursor:"pointer",
                background: i===0 ? "var(--grade-s)" : S(0.025),
                border: `1px solid ${i===0 ? "rgba(192,132,252,.3)" : B(0.06)}`,
              }}
              whileHover={{ background:"var(--grade-s)", borderColor:"rgba(192,132,252,.3)" }}>
                <div style={{ width:"100%", height:28, borderRadius:6, marginBottom:6, border:`1px solid ${B(0.07)}`,
                  background:`linear-gradient(90deg,hsl(${220+i*35},55%,10%),hsl(${240+i*30},45%,18%))` }} />
                <span style={{ fontSize:10, color:"var(--t2)", fontWeight:500 }}>{lut}</span>
              </motion.div>
            ))}
          </div>
        </div>

        {/* Color wheels */}
        <div style={{ flex:1, padding:14, borderRadius:12, background:S(0.03), border:`1px solid ${B(0.06)}`, display:"flex", gap:20, alignItems:"start" }}>
          {["Schatten","Mitten","Lichter"].map((zone,zi) => (
            <div key={zone} style={{ flex:1, display:"flex", flexDirection:"column", alignItems:"center", gap:10 }}>
              <span className="label">{zone}</span>
              <div style={{
                width:80, height:80, borderRadius:"50%", cursor:"crosshair",
                background:`conic-gradient(hsl(215,${55+zi*8}%,${12+zi*9}%) 0deg, hsl(255,${50+zi*6}%,${16+zi*8}%) 90deg, hsl(195,${52+zi*7}%,${14+zi*8}%) 180deg, hsl(235,${48+zi*5}%,${18+zi*7}%) 270deg, hsl(215,${55+zi*8}%,${12+zi*9}%) 360deg)`,
                border:"3px solid var(--b1)", boxShadow:"inset 0 0 24px rgba(0,0,0,.6)",
                display:"flex", alignItems:"center", justifyContent:"center",
              }}>
                <div style={{ width:10, height:10, borderRadius:"50%", background:"rgba(255,255,255,.6)", boxShadow:"0 0 6px rgba(0,0,0,.5)" }} />
              </div>
              {[{l:"L",v:50+zi*12},{l:"C",v:35+zi*10},{l:"H",v:60-zi*8}].map(({l,v}) => (
                <div key={l} style={{ width:"100%", display:"flex", alignItems:"center", gap:6 }}>
                  <span style={{ fontSize:10, color:"var(--t3)", width:12, textAlign:"right", fontFamily:"var(--mono)" }}>{l}</span>
                  <div style={{ flex:1, height:3, background:S(0.07), borderRadius:99, cursor:"pointer" }}>
                    <div style={{ height:"100%", width:`${v}%`, background:"var(--grade)", borderRadius:99 }} />
                  </div>
                  <span className="mono" style={{ color:"var(--t3)", fontSize:10, width:18 }}>{v}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════
   EFFETS
═════════════════════════════════════════════════════════ */
function EffetsTab() {
  const tools = [
    { id:"ext",   name:"Clip-Verlängerung",  sub:"Wan2.1 · temporal outpainting", color:"var(--fx)",    active:true,  badge:"Hauptfunktion" },
    { id:"stab",  name:"Stabilisierung",      sub:"Optical Flow · RAFT",           color:"var(--blue)",  active:false, badge:null },
    { id:"up",    name:"Upscaling 4K→8K",   sub:"Real-ESRGAN",                   color:"var(--gold)",  active:false, badge:null },
    { id:"roto",  name:"Auto-Rotoskopie",     sub:"SAM2 · Meta",                   color:"var(--cut)",   active:false, badge:null },
    { id:"music", name:"KI-Musik",            sub:"MusicGen · AudioCraft",         color:"var(--grade)", active:false, badge:null },
  ];
  const pipeline = [
    { n:1, name:"Extraktion",    sub:"FFmpeg",  color:"var(--blue)",  state:"done"    },
    { n:2, name:"Optical Flow",  sub:"RAFT",    color:"var(--gold)",  state:"done"    },
    { n:3, name:"Generierung",   sub:"Wan2.1",  color:"var(--fx)",    state:"running" },
    { n:4, name:"Zusammensetzung", sub:"FFmpeg",  color:"var(--cut)",   state:"pending" },
    { n:5, name:"Auswertung",    sub:"FVD+SSIM",color:"var(--grade)", state:"pending" },
  ];

  return (
    <div style={{ display:"flex", gap:12, height:"100%", minHeight:0 }}>
      {/* Tools */}
      <div style={{ width:230, flexShrink:0, display:"flex", flexDirection:"column", gap:4, overflowY:"auto" }}>
        <span className="label" style={{ marginBottom:4, display:"block" }}>KI-WERKZEUGE</span>
        {tools.map(t => (
          <motion.div key={t.id} style={{
            padding:"10px 12px", borderRadius:10, cursor:"pointer",
            background: t.active ? `color-mix(in srgb, ${t.color} 10%, transparent)` : S(0.025),
            border: `1px solid ${t.active ? `color-mix(in srgb, ${t.color} 25%, transparent)` : B(0.06)}`,
          }}
          whileHover={{ background:`color-mix(in srgb, ${t.color} 10%, transparent)`, borderColor:`color-mix(in srgb, ${t.color} 25%, transparent)` }}>
            <div style={{ display:"flex", alignItems:"start", justifyContent:"space-between", gap:8 }}>
              <div>
                <div style={{ display:"flex", alignItems:"center", gap:6, flexWrap:"wrap", marginBottom:2 }}>
                  <span style={{ fontSize:12, fontWeight:600, color:"var(--t0)", letterSpacing:"-0.01em" }}>{t.name}</span>
                  {t.badge && (
                    <span style={{
                      fontSize:9, fontWeight:600, letterSpacing:"0.04em",
                      color:t.color, padding:"1px 5px", borderRadius:4,
                      background:`color-mix(in srgb, ${t.color} 14%, transparent)`,
                      border:`1px solid color-mix(in srgb, ${t.color} 28%, transparent)`,
                    }}>{t.badge}</span>
                  )}
                </div>
                <span style={{ fontSize:11, color:"var(--t3)" }}>{t.sub}</span>
              </div>
              <div style={{ width:7, height:7, borderRadius:"50%", background: t.active ? t.color : B(0.12), marginTop:3, flexShrink:0 }} />
            </div>
          </motion.div>
        ))}
      </div>

      {/* Pipeline */}
      <div style={{ flex:1, display:"flex", flexDirection:"column", gap:10, minWidth:0 }}>
        {/* Feature callout */}
        <div style={{
          padding:"12px 16px", borderRadius:12,
          background:"linear-gradient(135deg,rgba(52,211,153,.07),rgba(52,211,153,.02))",
          border:"1px solid rgba(52,211,153,.2)",
        }}>
          <div style={{ display:"flex", alignItems:"center", gap:6, marginBottom:4 }}>
            <Expand size={13} style={{ color:"var(--fx)" }} />
            <span style={{ fontSize:13, fontWeight:600, color:"var(--fx)", letterSpacing:"-0.01em" }}>
              Intelligente Clip-Verlängerung
            </span>
          </div>
          <p style={{ fontSize:12, color:"var(--t2)", lineHeight:1.65 }}>
            Der Take dauert 3s, benötigt werden 5 — die KI analysiert die letzten Frames und generiert die Fortsetzung via{" "}
            <span style={{ color:"var(--t1)", fontWeight:500 }}>Wan2.1 (temporal outpainting)</span>.
          </p>
        </div>

        {/* Pipeline steps */}
        <div style={{ flex:1, padding:16, borderRadius:12, background:S(0.03), border:`1px solid ${B(0.06)}` }}>
          <span className="label" style={{ marginBottom:20, display:"block" }}>PIPELINE</span>
          <div style={{ display:"flex", alignItems:"start", gap:8, marginBottom:24, flexWrap:"wrap" }}>
            {pipeline.map((step,i) => (
              <div key={step.n} style={{ display:"flex", alignItems:"center", gap:8 }}>
                <div style={{ display:"flex", flexDirection:"column", alignItems:"center", gap:6 }}>
                  <motion.div style={{
                    width:44, height:44, borderRadius:14,
                    display:"flex", alignItems:"center", justifyContent:"center",
                    background: step.state==="pending" ? S(0.03) : `color-mix(in srgb, ${step.color} 16%, transparent)`,
                    border: `1px solid ${step.state==="pending" ? B(0.07) : `color-mix(in srgb, ${step.color} 35%, transparent)`}`,
                    color: step.state==="pending" ? "var(--t3)" : step.color,
                    fontSize:12, fontWeight:700, fontFamily:"var(--mono)",
                  }}
                  animate={step.state==="running" ? { boxShadow:["0 0 0px transparent","0 0 16px rgba(52,211,153,.4)","0 0 0px transparent"] } : {}}
                  transition={{ duration:2, repeat:Infinity }}>
                    {step.state==="done" ? <Check size={16} />
                     : step.state==="running" ? (
                       <motion.div animate={{ rotate:360 }} transition={{ duration:1.4, repeat:Infinity, ease:"linear" }}>
                         <Loader2 size={16} />
                       </motion.div>
                     ) : step.n}
                  </motion.div>
                  <div style={{ textAlign:"center", maxWidth:60 }}>
                    <div style={{ fontSize:11, color: step.state==="pending" ? "var(--t3)" : step.color, fontWeight:500, lineHeight:1.3 }}>
                      {step.name}
                    </div>
                    <div style={{ fontSize:9, color:"var(--t3)", fontFamily:"var(--mono)" }}>{step.sub}</div>
                  </div>
                </div>
                {i < pipeline.length-1 && (
                  <div style={{ width:22, height:1, background:"var(--b1)", marginBottom:24, flexShrink:0 }} />
                )}
              </div>
            ))}
          </div>

          {/* Progress */}
          <div style={{ padding:"10px 14px", borderRadius:10, background:S(0.03), border:`1px solid ${B(0.06)}` }}>
            <div style={{ display:"flex", justifyContent:"space-between", marginBottom:8, fontSize:12 }}>
              <span style={{ color:"var(--t2)" }}>Wan2.1-Generierung…</span>
              <span className="mono" style={{ color:"var(--t2)" }}>142 / 240 frames</span>
            </div>
            <div style={{ height:4, background:S(0.08), borderRadius:99, overflow:"hidden" }}>
              <motion.div
                style={{ height:"100%", background:"linear-gradient(90deg,var(--fx),var(--blue))", borderRadius:99 }}
                initial={{ width:0 }}
                animate={{ width:"59%" }}
                transition={{ duration:1, ease:[0.22,1,0.36,1] }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════
   PAGE
═════════════════════════════════════════════════════════ */
export default function ProjectPage() {
  const [tab, setTab]  = useState("montage");
  const [msg, setMsg]  = useState("");
  const activeTab = TABS.find(t => t.id === tab)!;

  return (
    <div style={{ display:"flex", height:"100dvh", background:"var(--bg)", overflow:"hidden" }}>
      <AppSidebar />

      {/* Ambient glow */}
      <div
        aria-hidden
        style={{
          position:"fixed", inset:0, pointerEvents:"none",
          background:`radial-gradient(ellipse 60% 40% at 65% 0%, color-mix(in srgb, ${activeTab.hex} 7%, transparent) 0%, transparent 65%)`,
          transition:"background 0.7s ease",
          zIndex:0,
        }}
      />

      {/* Main */}
      <div style={{
        marginLeft:"var(--sidebar-w)",
        marginBottom:"var(--bar-h)",
        flex:1, display:"flex", flexDirection:"column",
        overflow:"hidden", position:"relative", zIndex:1,
      }}>
        {/* ── Top toolbar ────────────────────────────── */}
        <div style={{
          display:"flex", alignItems:"center", gap:8, padding:"0 16px",
          height:46, borderBottom:"1px solid var(--b0)", background:"var(--bg-up)",
          flexShrink:0,
        }}>
          <Link href="/" style={{ textDecoration:"none" }}>
            <motion.div style={{
              width:28, height:28, borderRadius:8, display:"flex",
              alignItems:"center", justifyContent:"center",
              background:"var(--bg-raised)", border:"1px solid var(--b1)", cursor:"pointer",
            }}
            whileHover={{ borderColor:"var(--b2)" }} whileTap={{ scale:0.93 }}>
              <ArrowLeft size={12} style={{ color:"var(--t1)" }} />
            </motion.div>
          </Link>

          <div style={{ width:1, height:18, background:"var(--b1)", margin:"0 4px" }} />

          <div>
            <div style={{ fontSize:13, fontWeight:600, color:"var(--t0)", letterSpacing:"-0.015em" }}>
              L&apos;Heure Bleue
            </div>
          </div>

          <div style={{ display:"flex", alignItems:"center", gap:4, marginLeft:6 }}>
            <span style={{
              fontSize:10, padding:"2px 7px", borderRadius:5,
              background:"var(--gold-s)", border:"1px solid var(--gold-b)",
              color:"var(--gold)", fontWeight:600,
            }}>In Arbeit</span>
          </div>

          {/* Tab switcher */}
          <div style={{
            display:"flex", alignItems:"center", gap:0, marginLeft:12,
            background:"var(--bg-raised)", border:"1px solid var(--b1)", borderRadius:10, padding:"2px",
          }}>
            {TABS.map(t => {
              const Icon = t.icon;
              const active = tab === t.id;
              return (
                <motion.button key={t.id} onClick={() => setTab(t.id)}
                  style={{
                    position:"relative", display:"flex", alignItems:"center", gap:5,
                    padding:"4px 12px", borderRadius:8, cursor:"pointer",
                    color: active ? t.color : "var(--t2)",
                    background:"transparent", border:"none",
                  }}
                  whileTap={{ scale:0.96 }}
                >
                  {active && (
                    <motion.div layoutId="tab-bg" style={{
                      position:"absolute", inset:0, borderRadius:8,
                      background:`color-mix(in srgb, ${t.color} 12%, transparent)`,
                      border:`1px solid color-mix(in srgb, ${t.color} 28%, transparent)`,
                    }} transition={{ type:"spring", stiffness:500, damping:35 }} />
                  )}
                  <Icon size={12} style={{ position:"relative" }} strokeWidth={active ? 2 : 1.7} />
                  <span style={{ fontSize:12, fontWeight:active?600:400, letterSpacing:"-0.01em", position:"relative" }}>
                    {t.label}
                  </span>
                </motion.button>
              );
            })}
          </div>

          <div style={{ flex:1 }} />

          <div style={{ display:"flex", alignItems:"center", gap:4, fontSize:11, color:"var(--t3)", fontFamily:"var(--mono)" }}>
            <Film size={11} />
            64 clips · 18:24 · 4K · ProRes
          </div>

          <div style={{ width:1, height:18, background:"var(--b1)", margin:"0 4px" }} />

          <motion.button style={{
            display:"flex", alignItems:"center", gap:5, height:28, padding:"0 10px",
            borderRadius:8, background:"var(--bg-raised)", border:"1px solid var(--b1)",
            color:"var(--t1)", fontSize:12, cursor:"pointer",
          }}
          whileHover={{ borderColor:"var(--b2)", color:"var(--t0)" }} whileTap={{ scale:0.95 }}>
            <SlidersHorizontal size={11} />
            Exportieren
          </motion.button>
        </div>

        {/* ── Body ───────────────────────────────────── */}
        <div style={{ display:"flex", flex:1, minHeight:0 }}>

          {/* Center column */}
          <div style={{ flex:1, display:"flex", flexDirection:"column", minWidth:0, minHeight:0 }}>
            {/* Video player */}
            <div style={{ height:210, background:"#000", borderBottom:"1px solid var(--b0)", flexShrink:0, position:"relative" }}>
              <div style={{
                position:"absolute", inset:0,
                background:"linear-gradient(145deg,#060d1e 0%,#0b1f4a 35%,#152f70 60%,#060e22 100%)",
              }} />
              {/* Letterbox */}
              {[0,1].map(s => (
                <div key={s} style={{
                  position:"absolute", insetInline:0,
                  [s===0?"top":"bottom"]:0, height:18,
                  background:"rgba(0,0,0,0.85)",
                }} />
              ))}
              {/* Play */}
              <div style={{ position:"absolute", inset:0, display:"flex", alignItems:"center", justifyContent:"center" }}>
                <motion.button style={{
                  width:46, height:46, borderRadius:"50%", cursor:"pointer",
                  background:"rgba(255,255,255,0.1)", border:"1px solid rgba(255,255,255,0.2)",
                  backdropFilter:"blur(12px)", display:"flex", alignItems:"center", justifyContent:"center",
                }}
                whileHover={{ background:"rgba(255,255,255,.18)", scale:1.06 }} whileTap={{ scale:0.92 }}>
                  <div style={{ width:0, height:0, borderLeft:"15px solid rgba(255,255,255,.9)", borderTop:"9px solid transparent", borderBottom:"9px solid transparent", marginLeft:3 }} />
                </motion.button>
              </div>
              {/* TC */}
              <div className="mono" style={{ position:"absolute", bottom:24, left:16, fontSize:11, color:"rgba(255,255,255,.3)" }}>
                00:00:03:12
              </div>
              {/* Controls */}
              <div style={{
                position:"absolute", insetInline:0, bottom:0,
                display:"flex", alignItems:"center", gap:10, padding:"0 14px", height:36,
                background:"rgba(0,0,0,.65)", backdropFilter:"blur(16px)",
              }}>
                {[SkipBack, Play, SkipForward].map((Icon,i) => (
                  <motion.button key={i} whileTap={{ scale:0.88 }} style={{ background:"none", border:"none", cursor:"pointer" }}>
                    <Icon size={13} style={{ color: i===1 ? "rgba(255,255,255,.85)" : "rgba(255,255,255,.4)" }} />
                  </motion.button>
                ))}
                <div style={{ flex:1, height:3, background:"rgba(255,255,255,.14)", borderRadius:99, cursor:"pointer" }}>
                  <div style={{ width:"22%", height:"100%", background:"rgba(255,255,255,.7)", borderRadius:99 }} />
                </div>
                {[Volume2, Maximize2].map((Icon,i) => (
                  <motion.button key={i} whileTap={{ scale:0.9 }} style={{ background:"none", border:"none", cursor:"pointer" }}>
                    <Icon size={12} style={{ color:"rgba(255,255,255,.4)" }} />
                  </motion.button>
                ))}
              </div>
            </div>

            {/* Tab content */}
            <div style={{ flex:1, overflowY:"auto", padding:14, minHeight:0 }}>
              <AnimatePresence mode="wait">
                <motion.div key={tab} style={{ height:"100%" }}
                  initial={{ opacity:0, y:5 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0, y:-4 }}
                  transition={{ duration:0.18, ease:[0.22,1,0.36,1] }}>
                  {tab==="montage"    && <MontageTab />}
                  {tab==="etalonnage" && <EtalonnageTab />}
                  {tab==="effets"     && <EffetsTab />}
                </motion.div>
              </AnimatePresence>
            </div>
          </div>

          {/* ── AI Assistant ──────────────────────────── */}
          <div style={{
            width:264, flexShrink:0, borderLeft:"1px solid var(--b0)",
            background:"var(--bg-up)", display:"flex", flexDirection:"column",
          }}>
            {/* Header */}
            <div style={{ display:"flex", alignItems:"center", gap:8, padding:"10px 14px", borderBottom:"1px solid var(--b0)", flexShrink:0 }}>
              <div style={{
                width:26, height:26, borderRadius:8,
                background:"radial-gradient(circle at 35% 35%,rgba(212,168,83,0.18),rgba(212,168,83,0.05))",
                border:"1px solid rgba(212,168,83,0.26)",
                display:"flex", alignItems:"center", justifyContent:"center",
              }}>
                <BrainCircuit size={12} style={{ color:"var(--gold)" }} />
              </div>
              <div>
                <div style={{ fontSize:12, fontWeight:600, color:"var(--t0)", letterSpacing:"-0.01em" }}>KI-Assistent</div>
                <div style={{ fontSize:10, color:"var(--t3)" }}>LLaMA 3 · lokal</div>
              </div>
              <motion.div style={{ marginLeft:"auto", width:6, height:6, borderRadius:"50%", background:"var(--fx)", boxShadow:"0 0 8px var(--fx)" }}
                animate={{ opacity:[1,.3,1] }} transition={{ duration:2.5, repeat:Infinity }} />
            </div>

            {/* Context */}
            <div style={{ padding:"10px 12px", borderBottom:"1px solid var(--b0)", flexShrink:0 }}>
              <div style={{ display:"flex", alignItems:"center", gap:5, marginBottom:7 }}>
                <Layers size={10} style={{ color:"var(--t3)" }} />
                <span className="label">CONTEXTE</span>
              </div>
              <div style={{ display:"flex", flexWrap:"wrap", gap:4 }}>
                {["Skript importiert","64 Clips","Wan2.1 bereit","LLaMA 3 aktiv"].map(c => (
                  <span key={c} style={{
                    fontSize:10, fontWeight:500, padding:"2px 7px", borderRadius:5,
                    background:S(0.04), border:`1px solid ${B(0.07)}`, color:"var(--t2)",
                  }}>{c}</span>
                ))}
              </div>
            </div>

            {/* Messages */}
            <div style={{ flex:1, overflowY:"auto", padding:"10px 12px", display:"flex", flexDirection:"column", gap:10 }}>
              {MSGS.map((m,i) => (
                <motion.div key={i} style={{ display:"flex", justifyContent: m.r==="user" ? "flex-end" : "flex-start" }}
                  initial={{ opacity:0, y:8 }} animate={{ opacity:1, y:0 }} transition={{ delay:i*0.12+0.2 }}>
                  <div style={{
                    maxWidth:"90%", padding:"8px 11px", fontSize:12, lineHeight:1.65,
                    color: m.r==="user" ? "#eac97a" : "var(--t1)",
                    background: m.r==="user"
                      ? "radial-gradient(circle at 35% 35%,rgba(212,168,83,0.13),rgba(212,168,83,0.05))"
                      : S(0.04),
                    border: `1px solid ${m.r==="user" ? "rgba(212,168,83,0.25)" : B(0.07)}`,
                    borderRadius: m.r==="user" ? "14px 14px 3px 14px" : "14px 14px 14px 3px",
                  }}>{m.t}</div>
                </motion.div>
              ))}
            </div>

            {/* Input */}
            <div style={{ padding:"10px 12px", borderTop:"1px solid var(--b0)", flexShrink:0 }}>
              <div style={{ display:"flex", alignItems:"end", gap:7, padding:"8px 10px", borderRadius:12, background:"var(--bg-raised)", border:"1px solid var(--b1)" }}>
                <ZoomIn size={11} style={{ color:"var(--t3)", marginBottom:2, flexShrink:0 }} />
                <textarea rows={2} value={msg} onChange={e=>setMsg(e.target.value)}
                  placeholder="KI fragen…"
                  style={{
                    flex:1, background:"none", border:"none", outline:"none", resize:"none",
                    color:"var(--t0)", fontSize:12, lineHeight:1.6, caretColor:"var(--gold)",
                  }} />
                <motion.button style={{
                  width:26, height:26, borderRadius:8, flexShrink:0, cursor:"pointer",
                  display:"flex", alignItems:"center", justifyContent:"center",
                  background: msg ? "var(--gold-s)" : S(0.04),
                  border: `1px solid ${msg ? "var(--gold-b)" : B(0.07)}`,
                }}
                whileHover={msg ? { background:"rgba(212,168,83,.22)" } : {}} whileTap={{ scale:0.9 }}>
                  <Send size={10} style={{ color: msg ? "var(--gold)" : "var(--t3)" }} />
                </motion.button>
              </div>
              <div style={{ display:"flex", gap:5, marginTop:7, flexWrap:"wrap" }}>
                {["KI Rough Cut","LUT per Prompt","Clip verlängern","Exportieren"].map(s => (
                  <motion.button key={s} onClick={()=>setMsg(s)} style={{
                    fontSize:10, fontWeight:500, padding:"2px 8px", borderRadius:6,
                    background:S(0.04), border:`1px solid ${B(0.07)}`,
                    color:"var(--t2)", cursor:"pointer",
                    display:"flex", alignItems:"center", gap:4,
                  }}
                  whileHover={{ borderColor:B(0.16), color:"var(--t0)" }} whileTap={{ scale:0.95 }}>
                    <Music2 size={8} />{s}
                  </motion.button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <Dock />
    </div>
  );
}
