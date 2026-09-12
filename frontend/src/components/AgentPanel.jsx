import { useState, useEffect, useCallback } from "react"
import {
  Bot,
  Power,
  Play,
  Check,
  X,
  RefreshCw,
  ShieldCheck,
  Brain,
  Activity,
  TrendingUp,
  ChevronDown,
  ChevronUp,
} from "lucide-react"

const API = ""

const MODES = [
  { id: "OFF", label: "Off", desc: "Agent disabled — manual paper trading only" },
  { id: "SIGNAL_ONLY", label: "Signal Only", desc: "Agent sends alerts, never trades" },
  { id: "SEMI_AUTO", label: "Semi-Auto", desc: "Agent proposes — you approve each trade" },
  { id: "FULL_AUTO", label: "Full Auto", desc: "Agent trades automatically within guardrails" },
]

const fmtPct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`)

export default function AgentPanel() {
  const [status, setStatus] = useState(null)
  const [log, setLog] = useState([])
  const [queue, setQueue] = useState([])
      const [filters, setFilters] = useState(null)
  const [trades, setTrades] = useState(null)
  const [busy, setBusy] = useState(false)
  const [cycleResult, setCycleResult] = useState(null)
  const [expanded, setExpanded] = useState({})

  const refresh = useCallback(async () => {
    try {
            const [s, l, q, f, t] = await Promise.all([
        fetch(`${API}/api/agent/status`).then((r) => r.json()),
        fetch(`${API}/api/agent/log?limit=30`).then((r) => r.json()),
        fetch(`${API}/api/agent/queue?status=PENDING`).then((r) => r.json()),
        fetch(`${API}/api/agent/filters`).then((r) => r.json()),
        fetch(`${API}/api/agent/trades`).then((r) => r.json()),
      ])
      setStatus(s)
      setLog(l.log || [])
      setQueue(q.queue || [])
      setFilters(f)
      setTrades(t)
    } catch {
      /* non-fatal */
    }
  }, [])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 30000)
    return () => clearInterval(interval)
  }, [refresh])

  const setMode = async (mode) => {
    setBusy(true)
    try {
      await fetch(`${API}/api/agent/mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      })
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const runCycle = async () => {
    setBusy(true)
    setCycleResult(null)
    try {
      const res = await fetch(`${API}/api/agent/cycle/run`, { method: "POST" })
      setCycleResult(await res.json())
      await refresh()
    } catch (e) {
      setCycleResult({ ok: false, error: e.message })
    } finally {
      setBusy(false)
    }
  }

  const approve = async (id) => {
    setBusy(true)
    try {
      await fetch(`${API}/api/agent/approve/${id}`, { method: "POST" })
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const reject = async (id) => {
    setBusy(true)
    try {
      await fetch(`${API}/api/agent/reject/${id}`, { method: "POST" })
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  if (!status) {
    return (
      <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 text-slate-400 flex items-center gap-2">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading agent status…
      </div>
    )
  }

  const mode = status.mode || "OFF"
  const cfg = status.config || {}
  const g = status.guardrails || { ok: true, reasons: [] }
  const modeColor =
    mode === "OFF"
      ? "bg-slate-700/30 text-slate-300 border-slate-600/40"
      : mode === "SIGNAL_ONLY"
        ? "bg-sky-500/10 text-sky-300 border-sky-500/30"
        : mode === "SEMI_AUTO"
          ? "bg-amber-500/10 text-amber-300 border-amber-500/30"
          : "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"

  return (
    <div className="space-y-4">
      {/* Mode selector */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-emerald-300" />
            <h2 className="text-lg font-semibold">Autonomous Trading Agent</h2>
            <span className={`px-2 py-0.5 rounded-full text-xs border ${modeColor}`}>
              {MODES.find((m) => m.id === mode)?.label || mode}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={runCycle}
              disabled={busy || mode === "OFF"}
              title={mode === "OFF" ? "Turn the agent on first" : "Run one decision cycle now"}
              className={`inline-flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg border ${
                busy || mode === "OFF"
                  ? "bg-slate-800/40 border-slate-700 text-slate-500"
                  : "bg-emerald-500/15 border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/25"
              }`}
            >
              <Play className="w-3.5 h-3.5" /> Run cycle
            </button>
            <button
              onClick={refresh}
              className="inline-flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg bg-slate-800/60 border border-slate-700 hover:bg-slate-700/60"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
        <div className="p-5 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          {MODES.map((m) => (
            <button
              key={m.id}
              onClick={() => setMode(m.id)}
              disabled={busy}
              className={`text-left p-3 rounded-xl border transition ${
                mode === m.id
                  ? "bg-emerald-500/15 border-emerald-500/50"
                  : "bg-slate-800/40 border-slate-700 hover:bg-slate-700/40"
              }`}
            >
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Power className="w-3.5 h-3.5" /> {m.label}
              </div>
              <p className="text-xs text-slate-400 mt-1 leading-snug">{m.desc}</p>
            </button>
          ))}
        </div>
      </section>

      {/* Guardrails + ML + risk */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <ShieldCheck className={`w-4 h-4 ${g.ok ? "text-emerald-400" : "text-rose-400"}`} /> Guardrails
          </div>
          <div className={`mt-2 text-lg font-bold ${g.ok ? "text-emerald-300" : "text-rose-300"}`}>
            {g.ok ? "All clear" : "Blocked"}
          </div>
          {g.reasons?.length > 0 && (
            <ul className="text-xs text-rose-300/80 mt-1 space-y-0.5">
              {g.reasons.map((r, i) => (
                <li key={i}>• {r}</li>
              ))}
            </ul>
          )}
          <div className="text-xs text-slate-500 mt-2 space-y-0.5">
            <div>Drawdown: {fmtPct(status.drawdown_pct)}</div>
            <div>Daily P&L: {fmtPct(status.daily_pnl_pct)}</div>
            <div>Max open: {cfg.max_open_positions} · Risk/trade: {cfg.risk_per_trade_pct}%</div>
          </div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Brain className="w-4 h-4 text-violet-300" /> ML Model
          </div>
          <div className="mt-2 text-lg font-bold text-violet-200">
            {status.ml?.model_loaded ? "Trained" : "Cold start (heuristic)"}
          </div>
          <div className="text-xs text-slate-500 mt-2 space-y-0.5">
            <div>Trained rows: {status.ml?.trained_rows ?? 0} / {status.ml?.min_train_rows ?? 60}</div>
            <div>Horizon: {status.ml?.horizon_days ?? 10}d · Blend: {Math.round((status.ml?.blend_weight_ml ?? 0.4) * 100)}%</div>
          </div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Activity className="w-4 h-4 text-sky-300" /> Strategy Config
          </div>
          <div className="text-xs text-slate-400 mt-2 space-y-0.5 leading-relaxed">
            <div>Entry: score ≥ {cfg.min_score} & ML ≥ {cfg.min_ml_prob}</div>
            <div>Exit: {cfg.stop_atr_mult}×ATR stop · {cfg.target_rr}R target · {cfg.trailing_pct}% trail · {cfg.time_stop_days}d time</div>
            <div>Score exit &lt; {cfg.score_exit_level} · Halts: {cfg.daily_loss_halt_pct}%/d, {cfg.drawdown_pause_pct}% DD</div>
          </div>
        </div>
      </section>

      {/* Trading Filters panel */}
      {filters && (
        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-3">
            <ShieldCheck className="w-4 h-4 text-emerald-300" /> Trading Filters
          </div>
          <div className="text-xs text-slate-400 space-y-1">
            <div>
              <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${
                filters.volatility_regime_ok === true
                  ? "bg-emerald-400"
                  : filters.volatility_regime_ok === false
                  ? "bg-rose-400"
                  : "bg-slate-500"
              }`}></span>
              Volatility regime:
              {filters.volatility_regime_ok === true && (
                <span className="text-emerald-300"> Allowed</span>
              )}
              {filters.volatility_regime_ok === false && (
                <span className="text-rose-300"> Blocked</span>
              )}
              {filters.volatility_regime_ok === null && (
                <span className="text-slate-400"> Unknown</span>
              )}
              {filters.vix_level != null && (
                <span className="text-slate-500">
                  {" "}· VIX {filters.vix_level.toFixed(1)} (panic ≥{filters.vix_high_threshold})
                </span>
              )}
            </div>
            <div>
              <span className={`inline-block w-2 h-2 rounded-full mr-1.5 bg-emerald-400`}></span>
              Liquidity gate: active · min 30d avg volume {" "}
              {filters.min_avg_volume.toLocaleString()} shares/day
            </div>
            {filters.vix_chop_current !== null && typeof filters.vix_chop_current !== "undefined" && (
              <div className="text-slate-500">
                VIX 20d stdev: {filters.vix_chop_current.toFixed(1)}% (whipsaw blocks ≥{filters.vix_chop_threshold}%)
              </div>
            )}
          </div>
        </section>
      )}

      {trades && (
        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-3">
            <TrendingUp className="w-4 h-4 text-emerald-300" /> Recent Agent Trades
          </div>
          {trades.stats && (
            <div className="text-xs text-slate-400 space-y-1 mb-3 pb-2 border-b border-slate-800">
              <div>
                <span className="text-slate-500">Win rate:</span>{" "}
                <span className={`font-mono ${trades.stats.win_rate_pct >= 50 ? "text-emerald-300" : "text-rose-300"}`}>
                  {trades.stats.win_rate_pct.toFixed(1)}%
                </span>{" "}
                ({trades.stats.wins}W - {trades.stats.losses}L · {trades.stats.count} trades)
              </div>
              <div>
                <span className="text-slate-500">Total P&L:</span>{" "}
                <span className={`font-mono ${(trades.stats.total_pnl_eur || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                  €{(trades.stats.total_pnl_eur || 0).toFixed(2)}
                </span>{" "}
                · Avg hold: {trades.stats.avg_hold_days.toFixed(1)}d
              </div>
            </div>
          )}
          {trades.trades?.length > 0 && (
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {trades.trades.map((t) => (
                <div key={t.id} className="flex items-center justify-between text-xs">
                  <span className="font-mono font-semibold">{t.symbol}</span>
                  <span
                    className={`px-1.5 py-0.5 rounded ${
                      (t.pnl_eur || 0) > 0
                        ? "bg-emerald-500/10 text-emerald-300"
                        : "bg-rose-500/10 text-rose-300"
                    }`}
                  >
                    €{(t.pnl_eur || 0).toFixed(2)} ({(t.pnl_pct || 0).toFixed(1)}%)
                  </span>
                  <span className="text-slate-500">
                    {t.exit_reason} · {Number(t.hold_days).toFixed(1)}d
                  </span>
                </div>
              ))}
            </div>
          )}
          {trades.trades?.length === 0 && (
            <div className="text-xs text-slate-500 text-center py-4">
              No closed agent trades yet. Trades appear here once the agent opens and closes positions.
            </div>
          )}
        </section>
      )}
      {queue.length > 0 && (
        <section className="bg-amber-500/5 border border-amber-500/30 rounded-2xl overflow-hidden">
          <div className="px-5 py-4 border-b border-amber-500/20 flex items-center gap-2">
            <h2 className="text-lg font-semibold text-amber-200">
              Pending Proposals ({queue.length}) — your approval required
            </h2>
          </div>
          <div className="divide-y divide-slate-800">
            {queue.map((q) => (
              <div key={q.id} className="px-5 py-3 flex items-center justify-between gap-3 flex-wrap">
                <div className="text-sm">
                  <span className="font-mono font-semibold">{q.symbol}</span>
                  <span className="text-slate-400">
                    {" "}· {q.shares} sh @ {Number(q.price).toFixed(2)} · stop {Number(q.stop_loss).toFixed(2)}
                  </span>
                  <div className="text-xs text-slate-500 mt-0.5">{q.rationale}</div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => approve(q.id)}
                    disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/25"
                  >
                    <Check className="w-3.5 h-3.5" /> Approve
                  </button>
                  <button
                    onClick={() => reject(q.id)}
                    disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-rose-500/10 border border-rose-500/40 text-rose-200 hover:bg-rose-500/20"
                  >
                    <X className="w-3.5 h-3.5" /> Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Last cycle result */}
      {cycleResult && (
        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
          <h3 className="text-sm font-semibold text-slate-300 mb-2">Last cycle result</h3>
          <div className="text-xs text-slate-400 space-y-1">
            <div>
              Mode: <span className="font-mono">{cycleResult.mode}</span>
              {cycleResult.error && <span className="text-rose-300"> · error: {cycleResult.error}</span>}
            </div>
            <div>
              Entries: {cycleResult.entries?.length ?? 0} · Exits: {cycleResult.exits?.length ?? 0} ·
              Signals: {cycleResult.signals?.length ?? 0} · Skipped: {cycleResult.skipped?.length ?? 0}
            </div>
            {cycleResult.entries?.map((e, i) => (
              <div key={i} className="text-emerald-300">
                ▸ OPENED {e.shares} {e.symbol} @ {Number(e.price).toFixed(2)} {e.executed ? "" : `(failed: ${e.result?.error})`}
              </div>
            ))}
            {cycleResult.exits?.map((e, i) => (
              <div key={i} className="text-amber-300">
                ▸ CLOSED {e.symbol}: {e.reason} {e.executed ? "" : "(failed)"}
              </div>
            ))}
          </div>
        </section>
      )}


      {/* Decision log */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center gap-2">
          <h2 className="text-lg font-semibold">Decision Log</h2>
          <span className="text-xs text-slate-500">(every decision — including "did not trade")</span>
        </div>
        <div className="max-h-96 overflow-y-auto divide-y divide-slate-800/70">
          {log.length === 0 ? (
            <div className="px-5 py-8 text-sm text-slate-500 text-center">
              No agent activity yet. Pick a mode above and run a cycle.
            </div>
          ) : (
            log.map((l) => {
              const d = l.details || {}
              const key = l.id
              const isOpen = expanded[key]
              const summary =
                d.reasons
                  ? `${(d.reasons || []).slice(0, 2).join("; ")}${(d.reasons || []).length > 2 ? "…" : ""}`
                  : d.reason ||
                    (d.shares ? `${d.shares} sh @ ${d.price}` : d.error || JSON.stringify(d).slice(0, 90))
              return (
                <div key={key} className="px-5 py-2.5 hover:bg-slate-800/30">
                  <button
                    onClick={() => setExpanded((s) => ({ ...s, [key]: !s[key] }))}
                    className="w-full text-left flex items-center gap-2 text-sm"
                  >
                    {isOpen ? (
                      <ChevronUp className="w-3.5 h-3.5 text-slate-500" />
                    ) : (
                      <ChevronDown className="w-3.5 h-3.5 text-slate-500" />
                    )}
                    <span className="text-xs text-slate-500 font-mono w-36 shrink-0">
                      {(l.timestamp || "").slice(5, 16).replace("T", " ")}
                    </span>
                    <span
                      className={`px-1.5 py-0.5 rounded text-xs font-mono ${
                        l.action?.includes("entry")
                          ? "bg-emerald-500/10 text-emerald-300"
                          : l.action?.includes("exit")
                            ? "bg-amber-500/10 text-amber-300"
                            : "bg-slate-700/40 text-slate-300"
                      }`}
                    >
                      {l.action}
                    </span>
                    {l.symbol && <span className="font-mono font-semibold">{l.symbol}</span>}
                    <span className="text-xs text-slate-400 truncate">{summary}</span>
                    {l.executed ? <Check className="w-3.5 h-3.5 text-emerald-400 ml-auto shrink-0" /> : null}
                  </button>
                  {isOpen && (
                    <pre className="mt-2 text-xs text-slate-400 bg-slate-950/60 border border-slate-800 rounded-lg p-3 overflow-x-auto">
                      {JSON.stringify(d, null, 2)}
                    </pre>
                  )}
                </div>
              )
            })
          )}
        </div>
      </section>

      <p className="text-xs text-slate-500 text-center pb-4">
        The agent is deterministic rules + ML probability — every decision can be audited and backtested.
        Start with SIGNAL_ONLY, validate 4–6 weeks of paper performance, then consider SEMI_AUTO.
        No agent can guarantee profits — its job is disciplined, risk-capped execution.
      </p>
    </div>
  )
}

