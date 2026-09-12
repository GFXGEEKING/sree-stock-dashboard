import { useState, useEffect, useCallback, useMemo } from "react"
import {
  Wallet,
  Play,
  X,
  History,
  Check,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  RotateCcw,
  Calculator,
  LineChart,
} from "lucide-react"
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
  Filler,
} from "chart.js"
import { Line } from "react-chartjs-2"

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, Filler)

const API = ""

const fmtEur = (v) => {
  if (v == null || Number.isNaN(v)) return "—"
  return `€${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

const fmtPct = (v) => {
  if (v == null || Number.isNaN(v)) return "—"
  return `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`
}

const fmtEur0 = (v) => {
  if (v == null || Number.isNaN(v)) return "—"
  return Number(v).toFixed(2)
}

export default function PaperTradingPanel() {
  const [portfolio, setPortfolio] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)
  const [showHistory, setShowHistory] = useState(false)
  const [topStocks, setTopStocks] = useState([])
  const [topLoading, setTopLoading] = useState(true)
  const [regionFilter, setRegionFilter] = useState("All")
  const [search, setSearch] = useState("")

  // New order form state
  const [form, setForm] = useState({
    symbol: "",
    shares: 1,
    price: "",
    stop_loss: "",
    target_price: "",
  })
  // Close form per position
  const [closeForm, setCloseForm] = useState({})
  // Equity curve data
  const [equityCurve, setEquityCurve] = useState(null)
  // Size suggestion result
  const [suggestion, setSuggestion] = useState(null)
  // Performance metrics (Sharpe/Sortino/drawdown/expectancy/PF)
  const [metrics, setMetrics] = useState(null)

  const fetchMetrics = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/paper/metrics`)
      if (!res.ok) return
      setMetrics(await res.json())
    } catch (e) {
      // non-fatal
    }
  }, [])

  useEffect(() => {
    fetchMetrics()
    const interval = setInterval(fetchMetrics, 60000)
    return () => clearInterval(interval)
  }, [fetchMetrics])

  const fetchPortfolio = useCallback(async () => {
    try {
      setLoading(true)
      const res = await fetch(`${API}/api/paper/portfolio`)
      if (!res.ok) throw new Error(`Server ${res.status}`)
      const data = await res.json()
      setPortfolio(data)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchTopStocks = useCallback(async (region) => {
    try {
      setTopLoading(true)
      const regionParam = region && region !== "All" ? `?region=${encodeURIComponent(region)}` : ""
      const res = await fetch(`${API}/api/dashboard${regionParam}`)
      if (!res.ok) throw new Error(`Server ${res.status}`)
      const data = await res.json()
      setTopStocks((data.rankings || []).slice(0, 25))
    } catch (e) {
      setTopStocks([])
    } finally {
      setTopLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPortfolio()
    const interval = setInterval(fetchPortfolio, 30000)
    return () => clearInterval(interval)
  }, [fetchPortfolio])

  // Equity curve loads with portfolio
  const fetchEquityCurve = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/paper/equity-curve`)
      if (!res.ok) return
      setEquityCurve(await res.json())
    } catch (e) {
      // non-fatal
    }
  }, [])

  useEffect(() => {
    fetchEquityCurve()
    const interval = setInterval(fetchEquityCurve, 60000)
    return () => clearInterval(interval)
  }, [fetchEquityCurve])

  useEffect(() => {
    fetchTopStocks(regionFilter)
    const interval = setInterval(() => fetchTopStocks(regionFilter), 60000)
    return () => clearInterval(interval)
  }, [fetchTopStocks, regionFilter])

  const showToast = (msg, ok = true) => {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 3500)
  }

  // One-click prefill from the Top 25 scanner list
  const prefillOrder = (s) => {
    const price = s.current_price || s.price
    if (!price) {
      showToast("No live price available for this ticker", false)
      return
    }
    setForm({
      symbol: s.ticker,
      shares: 1,
      price: String(price),
      // Suggested guardrails: -6% stop, +15% target (swing-trade defaults)
      stop_loss: String((price * 0.94).toFixed(2)),
      target_price: String((price * 1.15).toFixed(2)),
    })
    showToast(`Prefilled order for ${s.ticker} — check size and submit`, true)
  }

  // Ask the backend for a risk-based share-count suggestion
  const suggestSize = async (override = {}) => {
    const symbol = override.symbol ?? form.symbol
    const price = override.price ?? form.price
    const stop_loss = override.stop_loss ?? form.stop_loss
    if (!symbol || !price || !stop_loss) {
      showToast("Fill symbol, price and stop-loss first", false)
      return
    }
    try {
      const body = {
        symbol: symbol.trim().toUpperCase(),
        price: parseFloat(price),
        stop_loss: parseFloat(stop_loss),
      }
      const target = override.target_price ?? form.target_price
      if (target) body.target_price = parseFloat(target)
      const res = await fetch(`${API}/api/paper/suggest-size`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || "suggestion failed")
      setSuggestion(data)
      if (data.shares > 0) {
        setForm((f) => ({ ...f, shares: String(data.shares) }))
        showToast(`Suggested ${data.shares} shares (risk €${data.risk_eur})`, true)
      } else {
        showToast("Suggestion: 0 shares — stop too wide for 1.5% risk", false)
      }
    } catch (e) {
      showToast(e.message, false)
    }
  }

  // ⚡ Quick: prefill the order form AND auto-size it in one click
  const quickTrade = (s) => {
    const price = s.current_price || s.price
    if (!price) {
      showToast("No live price available for this ticker", false)
      return
    }
    const stop = String((price * 0.94).toFixed(2))
    const target = String((price * 1.15).toFixed(2))
    setForm({
      symbol: s.ticker,
      shares: 1,
      price: String(price),
      stop_loss: stop,
      target_price: target,
    })
    suggestSize({ symbol: s.ticker, price: String(price), stop_loss: stop, target_price: target })
    showToast(`${s.ticker} prefilled + auto-sized — review and submit`, true)
  }

  const submitOrder = async (e) => {
    e.preventDefault()
    try {
      const body = {
        symbol: form.symbol.trim().toUpperCase(),
        shares: parseInt(form.shares, 10),
        price: parseFloat(form.price),
      }
      if (form.stop_loss) body.stop_loss = parseFloat(form.stop_loss)
      if (form.target_price) body.target_price = parseFloat(form.target_price)
      const res = await fetch(`${API}/api/paper/open`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.error || "Order rejected")
      showToast(`Bought ${body.shares} ${body.symbol} @ ${data.fill_price}`, true)
      setForm({ symbol: "", shares: 1, price: "", stop_loss: "", target_price: "" })
      fetchPortfolio()
    } catch (e) {
      showToast(e.message, false)
    }
  }

  const closePosition = async (position) => {
    const priceStr = closeForm[position.id]
    const price = parseFloat(priceStr)
    if (!priceStr || Number.isNaN(price) || price <= 0) {
      showToast("Enter a valid exit price first", false)
      return
    }
    try {
      const res = await fetch(`${API}/api/paper/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position_id: position.id, exit_price: price }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.error || "Close rejected")
      showToast(`Closed ${position.symbol}: P&L ${fmtEur(data.pnl_eur)}`, data.pnl_eur >= 0)
      fetchPortfolio()
    } catch (e) {
      showToast(e.message, false)
    }
  }

  const resetAccount = async () => {
    if (!window.confirm("Reset paper account to €10,000? All positions and history will be wiped.")) return
    try {
      const res = await fetch(`${API}/api/paper/reset`, { method: "POST" })
      if (!res.ok) throw new Error("Reset failed")
      showToast("Account reset to €10,000", true)
      fetchPortfolio()
    } catch (e) {
      showToast(e.message, false)
    }
  }

  const stats = portfolio?.stats || {}
  const positions = portfolio?.open_positions || []
  const history = portfolio?.trade_history || []
  const pnlColor = (v) => (v > 0 ? "text-emerald-400" : v < 0 ? "text-rose-400" : "text-slate-400")
  // Grade badge color per pick grade
  const gradeColor = (g) => ({
    STRONG: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    GOOD: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    FAIR: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    WEAK: "bg-orange-500/15 text-orange-300 border-orange-500/40",
    POOR: "bg-rose-500/15 text-rose-300 border-rose-500/40",
  }[g] || "bg-slate-800 text-slate-300 border-slate-600/40")

  // Per-trade R multiple: net P&L / initial risk (|entry - stop| × shares). Long: stop below entry; short: above.
  const rMult = (t) => {
    const pnl = Number(t.pnl_eur)
    const entry = Number(t.entry_price)
    const stop = Number(t.stop_loss)
    const shares = Number(t.shares)
    if (!Number.isFinite(pnl) || !Number.isFinite(entry) || !Number.isFinite(stop) || !shares) return null
    const side = String(t.side || "BUY").toUpperCase()
    const riskPerShare = side === "SELL" || side === "SHORT" ? stop - entry : entry - stop
    const totalRisk = riskPerShare * shares
    if (totalRisk <= 0) return null
    return pnl / totalRisk
  }

  // Equity curve chart data
  const equityChartData = useMemo(() => {
    if (!equityCurve || !equityCurve.points || equityCurve.points.length < 2) return null
    const pts = equityCurve.points
    return {
      labels: pts.map((_, i) => (i === 0 ? "Start" : i === pts.length - 1 ? "Now" : `#${i}`)),
      datasets: [
        {
          label: "Equity (€)",
          data: pts.map((p) => p.equity),
          borderColor: "rgb(56, 189, 248)",
          backgroundColor: "rgba(56, 189, 248, 0.12)",
          borderWidth: 2,
          pointRadius: pts.length > 30 ? 0 : 3,
          tension: 0.25,
          fill: true,
        },
      ],
    }
  }, [equityCurve])

  const equityChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "#cbd5e1", boxWidth: 14 } },
      tooltip: {
        callbacks: {
          title: (items) => {
            const i = items[0].dataIndex
            const p = equityCurve?.points?.[i]
            return p?.label || ""
          },
        },
      },
    },
    scales: {
      x: { grid: { color: "rgba(148, 163, 184, 0.08)" }, ticks: { color: "#94a3b8" } },
      y: {
        grid: { color: "rgba(148, 163, 184, 0.08)" },
        ticks: { color: "#94a3b8", callback: (v) => `€${Number(v).toLocaleString()}` },
      },
    },
  }

  return (
    <div className="space-y-6">
      {toast && (
        <div
          className={`fixed top-20 right-6 z-50 px-4 py-3 rounded-xl border shadow-lg backdrop-blur animate-pulse ${
            toast.ok
              ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-200"
              : "bg-rose-500/15 border-rose-500/40 text-rose-200"
          }`}
        >
          {toast.ok ? <Check className="w-4 h-4 inline mr-2" /> : <AlertCircle className="w-4 h-4 inline mr-2" />}
          {toast.msg}
        </div>
      )}

      {/* ===== Account summary ===== */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: "Cash", value: fmtEur(stats.cash), sub: `Start ${fmtEur(stats.starting_balance)}`, icon: Wallet },
          { label: "Equity", value: fmtEur(stats.equity), sub: `${fmtPct(stats.total_return_pct)} total return`, icon: TrendingUp },
          { label: "Open P&L", value: fmtEur(stats.unrealized_pnl), sub: `${positions.length} positions`, icon: History, color: pnlColor(stats.unrealized_pnl) },
          { label: "Realized P&L", value: fmtEur(stats.realized_pnl), sub: `Win rate ${stats.win_rate ?? 0}%`, icon: Check, color: pnlColor(stats.realized_pnl) },
        ].map((card, i) => (
          <div key={i} className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4">
            <div className="flex items-center gap-2 text-slate-400 text-xs uppercase tracking-wider">
              <card.icon className="w-3.5 h-3.5" /> {card.label}
            </div>
            <div className={`mt-2 text-xl font-bold ${card.color || ""}`}>{card.value}</div>
            <div className="text-xs text-slate-500 mt-1">{card.sub}</div>
          </div>
        ))}
      </section>

      {/* ===== New order form ===== */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-2xl">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Play className="w-4 h-4 text-emerald-400" /> Place Paper Order
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              €1 commission + 0.05% slippage per side · whole shares · min €200
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={fetchPortfolio}
              className="inline-flex items-center gap-2 px-3 py-1.5 bg-slate-800/60 border border-slate-700 rounded-lg text-sm hover:bg-slate-700/60"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
            </button>
            <button
              onClick={resetAccount}
              className="inline-flex items-center gap-2 px-3 py-1.5 bg-rose-500/10 border border-rose-500/30 text-rose-200 rounded-lg text-sm hover:bg-rose-500/20"
            >
              <RotateCcw className="w-3.5 h-3.5" /> Reset
            </button>
          </div>
        </div>
        <form onSubmit={submitOrder} className="p-5 grid grid-cols-1 md:grid-cols-6 gap-3 items-end">
          <div className="md:col-span-1">
            <label className="text-xs text-slate-400">Symbol</label>
            <input
              required
              value={form.symbol}
              onChange={(e) => setForm({ ...form, symbol: e.target.value })}
              placeholder="SAP.DE"
              className="w-full mt-1 px-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-sm focus:outline-none focus:border-sky-500"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400">Shares</label>
            <input
              required
              type="number"
              min="1"
              step="1"
              value={form.shares}
              onChange={(e) => setForm({ ...form, shares: e.target.value })}
              className="w-full mt-1 px-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-sm focus:outline-none focus:border-sky-500"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400">Entry Price</label>
            <input
              required
              type="number"
              step="0.01"
              min="0.01"
              value={form.price}
              onChange={(e) => setForm({ ...form, price: e.target.value })}
              placeholder="180.00"
              className="w-full mt-1 px-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-sm focus:outline-none focus:border-sky-500"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400">Stop Loss</label>
            <input
              type="number"
              step="0.01"
              min="0"
              value={form.stop_loss}
              onChange={(e) => setForm({ ...form, stop_loss: e.target.value })}
              placeholder="optional"
              className="w-full mt-1 px-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-sm focus:outline-none focus:border-sky-500"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400">Target</label>
            <input
              type="number"
              step="0.01"
              min="0"
              value={form.target_price}
              onChange={(e) => setForm({ ...form, target_price: e.target.value })}
              placeholder="optional"
              className="w-full mt-1 px-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-sm focus:outline-none focus:border-sky-500"
            />
          </div>
          <div className="flex flex-col gap-2">
            <button
              type="submit"
              className="px-4 py-2 bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 rounded-lg text-sm font-medium hover:bg-emerald-500/30"
            >
              Buy (Paper)
            </button>
            <button
              type="button"
              onClick={suggestSize}
              className="inline-flex items-center justify-center gap-1.5 px-4 py-2 bg-sky-500/15 border border-sky-500/40 text-sky-200 rounded-lg text-sm hover:bg-sky-500/25"
            >
              <Calculator className="w-3.5 h-3.5" /> Suggest Size
            </button>
          </div>
        </form>
        {suggestion && (
          <div className="px-5 pb-5 grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Suggested shares</div>
              <div className="font-bold text-sky-300">{suggestion.shares}</div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Position size</div>
              <div className="font-bold">{fmtEur(suggestion.size_eur)} ({suggestion.size_pct}%)</div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Risk if stopped</div>
              <div className="font-bold text-rose-300">{fmtEur(suggestion.risk_eur)} ({suggestion.risk_pct}%)</div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">R:R ratio</div>
              <div className={`font-bold ${suggestion.rr_acceptable ? "text-emerald-300" : "text-amber-300"}`}>
                {suggestion.rr_ratio ?? "—"} {suggestion.rr_acceptable ? "✓" : "(<2.0)"}
              </div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Half-Kelly</div>
              <div className="font-bold">{suggestion.half_kelly_frac}%</div>
            </div>
          </div>
        )}
      </section>

      {/* ===== Equity curve ===== */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-2xl">
        <div className="px-5 py-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <LineChart className="w-4 h-4 text-sky-400" /> Equity Curve
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              {equityCurve
                ? `€${Number(equityCurve.starting_balance).toLocaleString()} → €${Number(equityCurve.current_equity).toLocaleString()} (${equityCurve.total_return_pct > 0 ? "+" : ""}${equityCurve.total_return_pct}%) · Max DD €${Number(equityCurve.max_drawdown_eur || 0).toLocaleString()}`
                : "Loading…"}
            </p>
          </div>
        </div>
        <div className="p-5 h-64">
          {equityChartData ? (
            <Line data={equityChartData} options={equityChartOptions} />
          ) : (
            <div className="h-full flex items-center justify-center text-slate-500 text-sm">
              Close your first trade to build the curve
            </div>
          )}
        </div>
      </section>

      {/* ===== Top 25 scanner stocks (one-click order prefill) ===== */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Top 25 Scanner Stocks</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Showing {topStocks.length} recommendations, ranked best → weakest by composite score. Each row shows a quality grade and a brief reason for the pick.
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search symbol…"
              className="px-3 py-1.5 bg-slate-800/60 border border-slate-700 rounded-lg text-xs w-36 focus:outline-none focus:border-sky-500"
            />
            {["All", "Germany", "Europe", "USA"].map((r) => (
              <button
                key={r}
                onClick={() => setRegionFilter(r)}
                className={`px-2.5 py-1 text-xs rounded-lg border transition ${
                  regionFilter === r
                    ? "bg-sky-500/20 border-sky-500/50 text-sky-200"
                    : "bg-slate-800/40 border-slate-700 text-slate-300 hover:bg-slate-700/40"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        </div>
        {/* Grade legend + why hint */}
        <div className="px-5 py-2.5 border-b border-slate-800/70 flex flex-wrap items-center gap-2 text-xs text-slate-400">
          <span>Grade:</span>
          {["STRONG", "GOOD", "FAIR", "WEAK", "POOR"].map((g) => (
            <span key={g} className={`px-2 py-0.5 rounded border font-bold text-[10px] ${gradeColor(g)}`}>
              {g}
            </span>
          ))}
          <span className="ml-2 text-slate-500">💡 = why this pick</span>
        </div>
        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-sm">
            <thead className="bg-slate-900/80 text-slate-400 uppercase text-xs tracking-wider sticky top-0">
              <tr>
                <th className="text-left px-5 py-3">#</th>
                <th className="text-left px-3 py-3">Ticker</th>
                <th className="text-left px-3 py-3">Name</th>
                <th className="text-left px-3 py-3">Region</th>
                <th className="text-right px-3 py-3">Price</th>
                <th className="text-right px-3 py-3">Day %</th>
                <th className="text-right px-3 py-3">Score</th>
                <th className="text-center px-3 py-3">Grade</th>
                <th className="text-center px-5 py-3">Trade</th>
              </tr>
            </thead>
            <tbody>
              {topLoading && topStocks.length === 0 ? (
                <tr>
                  <td colSpan="9" className="text-center py-10 text-slate-400">
                    <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2" />
                    Loading scanner rankings…
                  </td>
                </tr>
              ) : topStocks.length === 0 ? (
                <tr>
                  <td colSpan="9" className="text-center py-10 text-slate-500">
                    No scanner data available — try Refresh or another region.
                  </td>
                </tr>
              ) : (
                topStocks
                  .filter(
                    (s) =>
                      !search ||
                      s.ticker.toLowerCase().includes(search.toLowerCase()) ||
                      (s.name || "").toLowerCase().includes(search.toLowerCase())
                  )
                  .map((s, i) => {
                  const change = s.daily_change
                  return (
                    <tr
                      key={s.ticker}
                      onClick={() => prefillOrder(s)}
                      className="border-t border-slate-800/70 cursor-pointer hover:bg-slate-800/40"
                    >
                      <td className="px-5 py-2">
                        <span
                          className={`w-6 h-6 rounded-md flex items-center justify-center text-xs font-bold ${
                            i < 3
                              ? "bg-amber-400/20 text-amber-300 border border-amber-400/40"
                              : "bg-slate-800 text-slate-300"
                          }`}
                        >
                          {i + 1}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <div className="font-mono font-semibold">{s.ticker}</div>
                        {s.summary && (
                          <div className="text-[10px] text-slate-500 mt-0.5 max-w-56 truncate" title={`💡 ${s.summary}`}>
                            💡 {s.summary}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 text-slate-200">{s.name || "—"}</td>
                      <td className="px-3 py-2 text-slate-400 text-xs">{s.region}</td>
                      <td className="px-3 py-2 text-right font-mono">{fmtEur0(s.current_price)}</td>
                      <td
                        className={`px-3 py-2 text-right font-mono ${
                          change > 0 ? "text-emerald-400" : change < 0 ? "text-rose-400" : "text-slate-400"
                        }`}
                      >
                        {change != null ? `${Number(change).toFixed(2)}%` : "—"}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-sky-300">
                        {s.composite_score != null ? Number(s.composite_score).toFixed(3) : "—"}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${gradeColor(s.grade)}`}>
                          {s.grade || "—"}
                        </span>
                      </td>
                      <td className="px-5 py-2 text-center">
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            quickTrade(s)
                          }}
                          className="px-2.5 py-1 bg-emerald-500/15 border border-emerald-500/40 text-emerald-200 rounded text-xs hover:bg-emerald-500/25"
                        >
                          <Play className="w-3 h-3 inline mr-1" />Quick
                        </button>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* ===== Open positions ===== */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800">
          <h2 className="text-lg font-semibold">Open Positions ({positions.length})</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-900/80 text-slate-400 uppercase text-xs tracking-wider">
              <tr>
                <th className="text-left px-5 py-3">Symbol</th>
                <th className="text-right px-3 py-3">Shares</th>
                <th className="text-right px-3 py-3">Entry</th>
                <th className="text-right px-3 py-3">Current</th>
                <th className="text-right px-3 py-3">Value</th>
                <th className="text-right px-3 py-3">Stop</th>
                <th className="text-right px-3 py-3">Target</th>
                <th className="text-right px-3 py-3">Unrealized</th>
                <th className="text-center px-5 py-3">Close</th>
              </tr>
            </thead>
            <tbody>
              {positions.length === 0 ? (
                <tr>
                  <td colSpan="9" className="text-center py-10 text-slate-500">
                    No open positions — place your first paper order above
                  </td>
                </tr>
              ) : (
                positions.map((p) => (
                  <tr key={p.id} className="border-t border-slate-800/70 hover:bg-slate-800/30">
                    <td className="px-5 py-3 font-mono font-semibold">{p.symbol}</td>
                    <td className="px-3 py-3 text-right font-mono">{p.shares}</td>
                    <td className="px-3 py-3 text-right font-mono">{Number(p.entry_price).toFixed(2)}</td>
                    <td className="px-3 py-3 text-right font-mono">{Number(p.current_price).toFixed(2)}</td>
                    <td className="px-3 py-3 text-right font-mono">{fmtEur(p.market_value)}</td>
                    <td className="px-3 py-3 text-right font-mono text-rose-300/80">
                      {p.stop_loss ? Number(p.stop_loss).toFixed(2) : "—"}
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-emerald-300/80">
                      {p.target_price ? Number(p.target_price).toFixed(2) : "—"}
                    </td>
                    <td className={`px-3 py-3 text-right font-mono ${pnlColor(p.unrealized_pnl)}`}>
                      {fmtEur(p.unrealized_pnl)}
                      <span className="block text-[10px] opacity-70">{fmtPct(p.unrealized_pnl_pct)}</span>
                    </td>
                    <td className="px-5 py-2">
                      <div className="flex items-center justify-center gap-1">
                        <input
                          type="number"
                          step="0.01"
                          min="0.01"
                          placeholder="exit €"
                          value={closeForm[p.id] || ""}
                          onChange={(e) => setCloseForm({ ...closeForm, [p.id]: e.target.value })}
                          className="w-20 px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs"
                        />
                        <button
                          onClick={() => closePosition(p)}
                          className="px-2 py-1 bg-rose-500/15 border border-rose-500/40 text-rose-200 rounded text-xs hover:bg-rose-500/25"
                        >
                          <X className="w-3 h-3 inline" /> Sell
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* ===== Performance metrics ===== */}
      {metrics && (
        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl">
          <div className="px-5 py-4 border-b border-slate-800">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <LineChart className="w-4 h-4 text-emerald-400" /> Performance Summary
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Risk-adjusted metrics and trade statistics across all closed paper trades.
            </p>
          </div>
          {metrics.trades?.edge_decay?.triggered && (
            <div className="mx-5 mt-4 px-4 py-3 rounded-xl border border-amber-500/40 bg-amber-500/10 text-amber-200 text-xs">
              ⚠️ {metrics.trades.edge_decay.message}
            </div>
          )}
          <div className="p-5 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3 text-sm">
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Sharpe</div>
              <div className={`font-bold ${metrics.risk?.sharpe == null ? "text-slate-500" : metrics.risk.sharpe >= 1 ? "text-emerald-300" : metrics.risk.sharpe >= 0 ? "text-amber-300" : "text-rose-300"}`}>
                {metrics.risk?.sharpe ?? "—"}
              </div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Sortino</div>
              <div className={`font-bold ${metrics.risk?.sortino == null ? "text-slate-500" : metrics.risk.sortino >= 1 ? "text-emerald-300" : metrics.risk.sortino >= 0 ? "text-amber-300" : "text-rose-300"}`}>
                {metrics.risk?.sortino ?? "—"}
              </div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Max Drawdown</div>
              <div className="font-bold text-rose-300">
                {metrics.risk?.max_drawdown_pct != null ? `${metrics.risk.max_drawdown_pct}%` : "—"}
              </div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Ann. Vol</div>
              <div className="font-bold">
                {metrics.risk?.volatility_annual_pct != null ? `${metrics.risk.volatility_annual_pct}%` : "—"}
              </div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Win Rate</div>
              <div className={`font-bold ${metrics.trades?.win_rate_pct >= 50 ? "text-emerald-300" : "text-rose-300"}`}>
                {metrics.trades?.win_rate_pct ?? 0}%
              </div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Expectancy</div>
              <div className={`font-bold ${(metrics.trades?.expectancy_eur ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                {metrics.trades?.expectancy_eur != null ? `€${metrics.trades.expectancy_eur}` : "—"}
              </div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Profit Factor</div>
              <div className={`font-bold ${(metrics.trades?.profit_factor ?? 0) >= 1 ? "text-emerald-300" : "text-rose-300"}`}>
                {metrics.trades?.profit_factor ?? "—"}
              </div>
            </div>
            <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-400">Closed Trades</div>
              <div className="font-bold">{metrics.trades?.closed_trades ?? 0}</div>
            </div>
          </div>

      <div className="px-5 pb-5 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3 text-sm">
            <div className="bg-slate-800/20 border border-slate-800 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-500">Avg Win</div>
              <div className="font-mono">{metrics.trades?.avg_win_eur != null ? `€${metrics.trades.avg_win_eur}` : "—"}</div>
            </div>
            <div className="bg-slate-800/20 border border-slate-800 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-500">Avg Loss</div>
              <div className="font-mono text-rose-300">{metrics.trades?.avg_loss_eur != null ? `€${metrics.trades.avg_loss_eur}` : "—"}</div>
            </div>
            <div className="bg-slate-800/20 border border-slate-800 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-500">Best Trade</div>
              <div className="font-mono text-emerald-300">{metrics.trades?.best_eur != null ? `€${metrics.trades.best_eur}` : "—"}</div>
            </div>
            <div className="bg-slate-800/20 border border-slate-800 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-500">Worst Trade</div>
              <div className="font-mono text-rose-300">{metrics.trades?.worst_eur != null ? `€${metrics.trades.worst_eur}` : "—"}</div>
            </div>
            <div className="bg-slate-800/20 border border-slate-800 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-500">Avg Hold</div>
              <div className="font-mono">{metrics.trades?.avg_hold_days != null ? `${metrics.trades.avg_hold_days}d` : "—"}</div>
            </div>
            <div className="bg-slate-800/20 border border-slate-800 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-500">Open Positions</div>
              <div className="font-mono">{metrics.current?.open_positions ?? 0}</div>
            </div>
            <div className="bg-slate-800/20 border border-slate-800 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-500">Avg R Multiple</div>
              <div className={`font-mono ${(metrics.trades?.avg_r_multiple ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                {metrics.trades?.avg_r_multiple != null ? `${metrics.trades.avg_r_multiple}R` : "—"}
              </div>
            </div>
            <div className="bg-slate-800/20 border border-slate-800 rounded-lg px-3 py-2">
              <div className="text-xs text-slate-500">vs {metrics.benchmark?.label ?? "S&P 500"}</div>
              <div className={`font-mono ${(metrics.benchmark?.outperform_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                {metrics.benchmark?.outperform_pct != null ? `${metrics.benchmark.outperform_pct > 0 ? "+" : ""}${metrics.benchmark.outperform_pct}%` : "—"}
              </div>
            </div>
          </div>
          {metrics.benchmark?.series?.length > 1 && (
            <div className="px-5 pb-5">
              <div className="text-xs text-slate-500 mb-2">
                Account vs {metrics.benchmark.label} (% return from start · last {metrics.benchmark.window_days}d)
              </div>
              <div className="h-40">
                <Line
                  data={{
                    labels: metrics.benchmark.series.map((s) => s.date),
                    datasets: [
                      {
                        label: "Account",
                        data: metrics.benchmark.series.map((s) => s.account_return_pct),
                        borderColor: "rgb(56, 189, 248)",
                        backgroundColor: "rgba(56, 189, 248, 0.10)",
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.25,
                        fill: true,
                      },
                      {
                        label: metrics.benchmark.label,
                        data: metrics.benchmark.series.map((s) => s.benchmark_return_pct),
                        borderColor: "rgb(148, 163, 184)",
                        borderWidth: 1.5,
                        pointRadius: 0,
                        tension: 0.25,
                        fill: false,
                      },
                    ],
                  }}
                  options={{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: "#cbd5e1", boxWidth: 12 } } },
                    scales: {
                      x: { grid: { color: "rgba(148,163,184,0.08)" }, ticks: { color: "#94a3b8", maxTicksLimit: 8 } },
                      y: { grid: { color: "rgba(148,163,184,0.08)" }, ticks: { color: "#94a3b8", callback: (v) => `${v}%` } },
                    },
                  }}
                />
              </div>
            </div>
          )}
          {metrics.trades?.closed_trades === 0 && (
            <div className="px-5 pb-5 text-xs text-slate-500">
              No closed trades yet — metrics populate as positions are closed.
            </div>
          )}
        </section>
      )}

      {/* ===== Trade history ===== */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Trade History</h2>
            <p className="text-xs text-slate-400">{history.length} trades · win rate {stats.win_rate ?? 0}% · avg hold {stats.avg_hold_days ?? 0}d</p>
          </div>
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="text-xs px-3 py-1.5 bg-slate-800/60 border border-slate-700 rounded-lg hover:bg-slate-700/60"
          >
            {showHistory ? "Hide" : "Show"}
          </button>
        </div>
        {showHistory && (
          <div className="overflow-x-auto max-h-80">
            <table className="w-full text-sm">
              <thead className="bg-slate-900/80 text-slate-400 uppercase text-xs tracking-wider sticky top-0">
                <tr>
                  <th className="text-left px-5 py-3">Symbol</th>
                  <th className="text-right px-3 py-3">Side</th>
                  <th className="text-right px-3 py-3">Shares</th>
                  <th className="text-right px-3 py-3">Entry</th>
                  <th className="text-right px-3 py-3">Exit</th>
                  <th className="text-right px-3 py-3">P&L (€)</th>
                  <th className="text-right px-3 py-3">P&L (%)</th>
                  <th className="text-right px-3 py-3">R</th>
                  <th className="text-right px-3 py-3">Hold</th>
                  <th className="text-right px-5 py-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 ? (
                  <tr>
                    <td colSpan="10" className="text-center py-10 text-slate-500">No trades yet</td>
                  </tr>
                ) : (
                  history.map((t) => (
                    <tr key={t.id} className="border-t border-slate-800/70">
                      <td className="px-5 py-2 font-mono">{t.symbol}</td>
                      <td className="px-3 py-2 text-right uppercase text-xs">{t.side}</td>
                      <td className="px-3 py-2 text-right font-mono">{t.shares}</td>
                      <td className="px-3 py-2 text-right font-mono">{t.entry_price ? Number(t.entry_price).toFixed(2) : "—"}</td>
                      <td className="px-3 py-2 text-right font-mono">{t.exit_price ? Number(t.exit_price).toFixed(2) : "—"}</td>
                      <td className={`px-3 py-2 text-right font-mono ${pnlColor(t.pnl_eur)}`}>
                        {t.pnl_eur != null ? fmtEur(t.pnl_eur) : "—"}
                      </td>
                      <td className={`px-3 py-2 text-right font-mono ${pnlColor(t.pnl_pct)}`}>
                        {t.pnl_pct != null ? fmtPct(t.pnl_pct) : "—"}
                      </td>
                      <td className={`px-3 py-2 text-right font-mono ${(() => { const r = rMult(t); return r == null ? "text-slate-500" : r > 0 ? "text-emerald-400" : r < 0 ? "text-rose-400" : "text-slate-400" })()}`}>
                        {(() => { const r = rMult(t); return r == null ? "—" : `${r > 0 ? "+" : ""}${r.toFixed(2)}R` })()}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-slate-400">{t.hold_days ?? "—"}d</td>
                      <td className="px-5 py-2 text-right">
                        <span
                          className={`px-2 py-0.5 rounded text-xs ${
                            t.status === "OPEN"
                              ? "bg-sky-500/10 text-sky-300 border border-sky-500/30"
                              : "bg-slate-700/30 text-slate-300 border border-slate-600/40"
                          }`}
                        >
                          {t.status}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="text-xs text-slate-500 text-center pb-4">
        Paper trading only — virtual money, no real orders. Practise 90 days before live trading.
      </p>
    </div>
  )
}
