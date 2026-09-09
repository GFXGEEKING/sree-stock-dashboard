import { useState, useEffect, useMemo } from "react"
import {
  TrendingUp,
  TrendingDown,
  Globe2,
  Activity,
  Sparkles,
  Search,
  RefreshCw,
  ChevronUp,
  AlertCircle,
  Wallet,
  CalendarDays,
  Bot,
} from "lucide-react"
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from "chart.js"
import { Line } from "react-chartjs-2"
import PaperTradingPanel from "./components/PaperTradingPanel"
import AgentPanel from "./components/AgentPanel"
import "./index.css"

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
)

const REGIONS = ["All", "Germany", "Europe", "USA"]
const API_BASE = ""

function fmt(v, d = 2) {
  if (v == null || Number.isNaN(v)) return "—"
  return Number(v).toFixed(d)
}

// Compact market-status pill (XETRA / LSE / NYSE)
function MarketPill({ name, status }) {
  const open = status?.is_open
  return (
    <div
      title={`${status?.name || name}: ${open ? "open" : "closed"}${status?.countdown_human ? ` · ${open ? "closes in" : "opens in"} ${status.countdown_human}` : ""} (local ${status?.local_time || ""})`}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs ${
        open
          ? "bg-emerald-500/10 border-emerald-500/40 text-emerald-300"
          : "bg-slate-800/60 border-slate-700 text-slate-400"
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${open ? "bg-emerald-400 animate-pulse" : "bg-slate-500"}`} />
      {name} {open ? "" : "·"}
    </div>
  )
}

export default function App() {
  const [regionFilter, setRegionFilter] = useState("All")
  const [selectedTicker, setSelectedTicker] = useState(null)
  const [rankings, setRankings] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState("")
  const [lastUpdated, setLastUpdated] = useState(null)
  const [history, setHistory] = useState(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [activeTab, setActiveTab] = useState("scanner")
  const [now, setNow] = useState(new Date())
  const [marketStatus, setMarketStatus] = useState(null)

  const fetchDashboard = async () => {
    try {
      setLoading(true)
      setError(null)
      const regionParam = regionFilter === "All" ? "" : `?region=${encodeURIComponent(regionFilter)}`
      const res = await fetch(`${API_BASE}/api/dashboard${regionParam}`)
      if (!res.ok) throw new Error(`Server returned ${res.status}`)
      const data = await res.json()
      setRankings(data.rankings || [])
      setLastUpdated(new Date())
      setSelectedTicker((current) => {
        if (current && data.rankings.find((r) => r.ticker === current)) return current
        return data.rankings[0]?.ticker || null
      })
    } catch (err) {
      setError(err.message || "Failed to fetch dashboard")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchDashboard()
    const interval = setInterval(fetchDashboard, 60000)
    return () => clearInterval(interval)
  }, [regionFilter])

  useEffect(() => {
    if (!selectedTicker) return
    const fetchHistory = async () => {
      try {
        setHistoryLoading(true)
        const res = await fetch(`${API_BASE}/api/forecast/${encodeURIComponent(selectedTicker)}?days=7`)
        if (!res.ok) throw new Error(`Server returned ${res.status}`)
        const data = await res.json()
        setHistory(data.forecast || null)
      } catch (e) {
        setHistory(null)
      } finally {
        setHistoryLoading(false)
      }
    }
    fetchHistory()
  }, [selectedTicker])

  // Live clock: date + time in the top bar
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  // Market open/closed status (refresh every 60s)
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/markets/status`)
        if (res.ok) setMarketStatus(await res.json())
      } catch (e) {
        // non-fatal
      }
    }
    load()
    const interval = setInterval(load, 60000)
    return () => clearInterval(interval)
  }, [])

  const statusOf = (key) =>
    (marketStatus?.markets || []).find((m) => m.market === key)

  const filteredRankings = useMemo(() => {
    let list = rankings
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(
        (r) =>
          (r.ticker || "").toLowerCase().includes(q) ||
          (r.name || "").toLowerCase().includes(q),
      )
    }
    return list
  }, [rankings, search])

  const top25 = filteredRankings.slice(0, 25)
  const selectedStock =
    filteredRankings.find((r) => r.ticker === selectedTicker) || filteredRankings[0]

  const chartData = useMemo(() => {
    if (!selectedStock || !history) return null
    const lastPrice = selectedStock.current_price
    const today = new Date().toISOString().slice(0, 10)
    const histLabels = [today]
    const histValues = [lastPrice]
    const forecastLabels = history.dates || []
    const forecastValues = history.prices || []
    const upper = history.upper || []
    const lower = history.lower || []

    const allLabels = [...histLabels, ...forecastLabels]
    const historicalSeries = [...histValues, ...new Array(forecastLabels.length).fill(null)]
    const bridge = new Array(Math.max(0, histLabels.length - 1)).fill(null)
    const forecastSeries = [...bridge, lastPrice, ...forecastValues]
    const upperSeries = [...bridge, lastPrice, ...upper]
    const lowerSeries = [...bridge, lastPrice, ...lower]
    const livePoint = forecastSeries.map((v, i) => (i === bridge.length ? v : null))

    return {
      labels: allLabels,
      datasets: [
        {
          label: "Live Anchor",
          data: historicalSeries,
          borderColor: "rgb(56, 189, 248)",
          backgroundColor: "rgba(56, 189, 248, 0.15)",
          borderWidth: 2,
          tension: 0.35,
          pointRadius: 0,
          fill: false,
        },
        {
          label: "Live Price (Now)",
          data: livePoint,
          borderColor: "rgb(250, 204, 21)",
          backgroundColor: "rgb(250, 204, 21)",
          showLine: false,
          pointRadius: 8,
          pointHoverRadius: 10,
          pointBorderColor: "#fff",
          pointBorderWidth: 2,
        },
        {
          label: "Upper Bound",
          data: upperSeries,
          borderColor: "rgba(34, 197, 94, 0.5)",
          backgroundColor: "rgba(34, 197, 94, 0.10)",
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          fill: "+1",
          tension: 0.3,
        },
        {
          label: "7-Day Forecast",
          data: forecastSeries,
          borderColor: "rgb(34, 197, 94)",
          backgroundColor: "rgba(34, 197, 94, 0.2)",
          borderWidth: 2,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
          tension: 0.3,
        },
        {
          label: "Lower Bound",
          data: lowerSeries,
          borderColor: "rgba(34, 197, 94, 0.5)",
          backgroundColor: "rgba(34, 197, 94, 0.10)",
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          fill: false,
          tension: 0.3,
        },
      ],
    }
  }, [selectedStock, history])

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: {
        position: "top",
        labels: { color: "#cbd5e1", boxWidth: 16, padding: 12 },
      },
      tooltip: {
        backgroundColor: "rgba(15, 23, 42, 0.95)",
        titleColor: "#f8fafc",
        bodyColor: "#cbd5e1",
        borderColor: "rgba(148, 163, 184, 0.3)",
        borderWidth: 1,
        padding: 12,
        callbacks: {
          label: (ctx) => {
            const v = ctx.parsed.y
            if (v == null) return null
            return `${ctx.dataset.label}: ${v.toFixed(2)}`
          },
        },
      },
    },
    scales: {
      x: {
        grid: { color: "rgba(148, 163, 184, 0.08)" },
        ticks: { color: "#94a3b8", maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
      },
      y: {
        grid: { color: "rgba(148, 163, 184, 0.08)" },
        ticks: { color: "#94a3b8" },
      },
    },
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800/80 bg-slate-900/60 backdrop-blur sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-6 py-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-sky-500 to-emerald-400 flex items-center justify-center shadow-lg shadow-sky-500/20">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight">Best of the Market</h1>
              <p className="text-xs text-slate-400">
                Multi-region momentum & value dashboard
                {lastUpdated && (
                  <span className="ml-2 text-slate-500">
                    · Updated {lastUpdated.toLocaleTimeString()}
                  </span>
                )}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {/* Date + time display */}
            <div className="hidden sm:flex items-center gap-2 px-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-sm">
              <CalendarDays className="w-4 h-4 text-sky-400" />
              <span className="font-medium text-slate-200">
                {now.toLocaleDateString(undefined, {
                  weekday: "short",
                  day: "2-digit",
                  month: "short",
                  year: "numeric",
                })}
              </span>
              <span className="text-slate-500">|</span>
              <span className="font-mono text-emerald-300">
                {now.toLocaleTimeString(undefined, {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </span>
            </div>
            {activeTab === "scanner" && (
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search ticker or name"
                  className="pl-9 pr-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-sm focus:outline-none focus:border-sky-500 w-56"
                />
              </div>
            )}
            <button
              onClick={fetchDashboard}
              className={`inline-flex items-center gap-2 px-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-sm hover:bg-slate-700/60 ${
                activeTab === "scanner" ? "" : "hidden sm:inline-flex"
              }`}
            >
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
              <span className="hidden sm:inline">Refresh</span>
            </button>
          </div>
        </div>
        {/* Tab navigation + market status pills */}
        <div className="max-w-7xl mx-auto px-6 pb-3 flex items-center gap-2 flex-wrap">
          <button
            onClick={() => setActiveTab("scanner")}
            className={`inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg border transition ${
              activeTab === "scanner"
                ? "bg-sky-500/20 border-sky-500/50 text-sky-200"
                : "bg-slate-800/40 border-slate-700 text-slate-300 hover:bg-slate-700/40"
            }`}
          >
            <Globe2 className="w-4 h-4" /> Scanner
          </button>
          <button
            onClick={() => setActiveTab("paper")}
            className={`inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg border transition ${
              activeTab === "paper"
                ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-200"
                : "bg-slate-800/40 border-slate-700 text-slate-300 hover:bg-slate-700/40"
            }`}
          >
            <Wallet className="w-4 h-4" /> Paper Trading
          </button>
          <button
            onClick={() => setActiveTab("agent")}
            className={`inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg border transition ${
              activeTab === "agent"
                ? "bg-violet-500/20 border-violet-500/50 text-violet-200"
                : "bg-slate-800/40 border-slate-700 text-slate-300 hover:bg-slate-700/40"
            }`}
          >
            <Bot className="w-4 h-4" /> Agent
          </button>
          <div className="ml-auto hidden md:flex items-center gap-2">
            <MarketPill name="XETRA 🇩🇪" status={statusOf("XETRA")} />
            <MarketPill name="LSE 🇬🇧" status={statusOf("LSE")} />
            <MarketPill name="NYSE 🇺🇸" status={statusOf("NYSE")} />
          </div>
        </div>
        {activeTab === "scanner" && (
          <div className="max-w-7xl mx-auto px-6 pb-3 flex items-center gap-2 flex-wrap">
            {REGIONS.map((r) => {
              const active = regionFilter === r
              return (
                <button
                  key={r}
                  onClick={() => setRegionFilter(r)}
                  className={`px-3 py-1.5 text-sm rounded-lg border transition ${
                    active
                      ? "bg-sky-500/20 border-sky-500/50 text-sky-200"
                      : "bg-slate-800/40 border-slate-700 text-slate-300 hover:bg-slate-700/40"
                  }`}
                >
                  {r}
                </button>
              )
            })}
          </div>
        )}
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">
        {activeTab === "paper" ? (
          <PaperTradingPanel />
        ) : activeTab === "agent" ? (
          <AgentPanel />
        ) : (
          <>
        {error && (
          <div className="bg-rose-500/10 border border-rose-500/40 text-rose-200 rounded-lg p-4 flex items-start gap-3">
            <AlertCircle className="w-5 h-5 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium">Error loading data</p>
              <p className="text-sm opacity-80">{error}</p>
            </div>
          </div>
        )}

        <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Globe2 className="w-4 h-4" /> Region Scope
            </div>
            <div className="mt-2 text-2xl font-bold">{regionFilter}</div>
            <div className="text-xs text-slate-500 mt-1">{filteredRankings.length} tickers in universe</div>
          </div>
          <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Activity className="w-4 h-4" /> Live Coverage
            </div>
            <div className="mt-2 text-2xl font-bold">
              {filteredRankings.filter((r) => r.current_price != null).length} / {filteredRankings.length}
            </div>
            <div className="text-xs text-slate-500 mt-1">Tickers with live prices</div>
          </div>
          <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Sparkles className="w-4 h-4" /> Top Leader
            </div>
            <div className="mt-2 text-2xl font-bold">
              {top25[0] ? `${top25[0].ticker} · ${top25[0].name || ""}` : "—"}
            </div>
            <div className="text-xs text-slate-500 mt-1">Score {fmt(top25[0]?.composite_score, 3)}</div>
          </div>
        </section>

        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold">Top 25 Market Leaders</h2>
              <p className="text-xs text-slate-400">Ranked by composite momentum + value score. Click a row to visualize.</p>
            </div>
            <span className="text-xs px-2 py-1 rounded-full bg-emerald-500/10 text-emerald-300 border border-emerald-500/30">
              Live · auto refresh 60s
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-900/80 text-slate-400 uppercase text-xs tracking-wider">
                <tr>
                  <th className="text-left px-5 py-3">#</th>
                  <th className="text-left px-3 py-3">Ticker</th>
                  <th className="text-left px-3 py-3">Name</th>
                  <th className="text-left px-3 py-3">Region</th>
                  <th className="text-right px-3 py-3">Price</th>
                  <th className="text-right px-3 py-3">Day %</th>
                  <th className="text-right px-3 py-3">Score</th>
                </tr>
              </thead>
              <tbody>
                {loading && filteredRankings.length === 0 ? (
                  <tr>
                    <td colSpan="7" className="text-center py-12 text-slate-400">
                      <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2" />
                      Loading market data…
                    </td>
                  </tr>
                ) : top25.length === 0 ? (
                  <tr>
                    <td colSpan="7" className="text-center py-12 text-slate-400">No stocks match your filter.</td>
                  </tr>
                ) : (
                  top25.map((s, i) => {
                    const isSelected = s.ticker === selectedTicker
                    const change = s.daily_change
                    return (
                      <tr
                        key={s.ticker}
                        onClick={() => setSelectedTicker(s.ticker)}
                        className={`border-t border-slate-800/70 cursor-pointer transition ${
                          isSelected ? "bg-sky-500/10" : "hover:bg-slate-800/40"
                        }`}
                      >
                        <td className="px-5 py-3">
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
                        <td className="px-3 py-3 font-mono font-semibold">{s.ticker}</td>
                        <td className="px-3 py-3 text-slate-200">{s.name || "—"}</td>
                        <td className="px-3 py-3">
                          <span
                            className={`text-xs px-2 py-0.5 rounded-full ${
                              s.region === "Germany"
                                ? "bg-amber-500/10 text-amber-300 border border-amber-500/30"
                                : s.region === "Europe"
                                ? "bg-violet-500/10 text-violet-300 border border-violet-500/30"
                                : s.region === "USA"
                                ? "bg-sky-500/10 text-sky-300 border border-sky-500/30"
                                : "bg-slate-800 text-slate-300"
                            }`}
                          >
                            {s.region}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right font-mono">{fmt(s.current_price)}</td>
                        <td
                          className={`px-3 py-3 text-right font-mono ${
                            change > 0 ? "text-emerald-400" : change < 0 ? "text-rose-400" : "text-slate-400"
                          }`}
                        >
                          {change != null ? (
                            <span className="inline-flex items-center gap-1 justify-end">
                              {change > 0 ? <TrendingUp className="w-3 h-3" /> : change < 0 ? <TrendingDown className="w-3 h-3" /> : null}
                              {fmt(change)}%
                            </span>
                          ) : "—"}
                        </td>
                        <td className="px-3 py-3 text-right font-mono text-sky-300">{fmt(s.composite_score, 3)}</td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl">
          <div className="px-5 py-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold flex items-center gap-2">
                <ChevronUp className="w-4 h-4 text-emerald-400" />
                {selectedStock ? `${selectedStock.name || selectedStock.ticker} (${selectedStock.ticker})` : "Select a stock"}
              </h2>
              <p className="text-xs text-slate-400">Live price (pulsing), 7-day forecast (dashed) with confidence bands.</p>
            </div>
            {selectedStock && (
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <span className="px-2 py-1 rounded bg-slate-800 border border-slate-700">Live: {fmt(selectedStock.current_price)}</span>
                <span className="px-2 py-1 rounded bg-slate-800 border border-slate-700">3M Mom: {fmt(selectedStock.momentum_3m, 1)}%</span>
                <span className="px-2 py-1 rounded bg-slate-800 border border-slate-700">6M Mom: {fmt(selectedStock.momentum_6m, 1)}%</span>
                <span className="px-2 py-1 rounded bg-slate-800 border border-slate-700">Fwd P/E: {fmt(selectedStock.forward_pe)}</span>
                <span className="px-2 py-1 rounded bg-slate-800 border border-slate-700">ROE: {fmt(selectedStock.roe, 1)}%</span>
              </div>
            )}
          </div>
          <div className="p-5 h-[420px] relative">
            {historyLoading && (
              <div className="absolute inset-0 flex items-center justify-center bg-slate-900/50 z-10 rounded-2xl">
                <RefreshCw className="w-6 h-6 animate-spin text-sky-400" />
              </div>
            )}
            {chartData ? (
              <Line data={chartData} options={chartOptions} />
            ) : (
              <div className="h-full flex items-center justify-center text-slate-500">
                {selectedStock ? "Forecast unavailable" : "Select a stock to visualize"}
              </div>
            )}
          </div>
        </section>

        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-800">
            <h2 className="text-lg font-semibold">Full Rankings</h2>
            <p className="text-xs text-slate-400">Top {Math.min(filteredRankings.length, 25)} of {filteredRankings.length} tickers</p>
          </div>
          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-sm">
              <thead className="bg-slate-900/80 text-slate-400 uppercase text-xs tracking-wider sticky top-0">
                <tr>
                  <th className="text-left px-5 py-3">Rank</th>
                  <th className="text-left px-3 py-3">Ticker</th>
                  <th className="text-left px-3 py-3">Name</th>
                  <th className="text-left px-3 py-3">Region</th>
                  <th className="text-right px-3 py-3">Price</th>
                  <th className="text-right px-3 py-3">Day %</th>
                  <th className="text-right px-3 py-3">3M %</th>
                  <th className="text-right px-3 py-3">6M %</th>
                  <th className="text-right px-3 py-3">Fwd P/E</th>
                  <th className="text-right px-3 py-3">ROE</th>
                  <th className="text-right px-3 py-3">RSI</th>
                  <th className="text-right px-3 py-3">Breakout</th>
                  <th className="text-right px-5 py-3">Signal</th>
                </tr>
              </thead>
              <tbody>
                {filteredRankings.slice(0, 25).map((s, i) => (
                  <tr
                    key={s.ticker}
                    onClick={() => setSelectedTicker(s.ticker)}
                    className={`border-t border-slate-800/70 cursor-pointer hover:bg-slate-800/40 ${
                      s.ticker === selectedTicker ? "bg-sky-500/10" : ""
                    }`}
                  >
                    <td className="px-5 py-2 font-mono">{i + 1}</td>
                    <td className="px-3 py-2 font-mono font-semibold">{s.ticker}</td>
                    <td className="px-3 py-2 text-slate-300">{s.name || "—"}</td>
                    <td className="px-3 py-2 text-slate-400">{s.region}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmt(s.current_price)}</td>
                    <td
                      className={`px-3 py-2 text-right font-mono ${
                        (s.daily_change ?? 0) > 0
                          ? "text-emerald-400"
                          : (s.daily_change ?? 0) < 0
                          ? "text-rose-400"
                          : "text-slate-400"
                      }`}
                    >
                      {s.daily_change != null ? `${fmt(s.daily_change)}%` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-300">{fmt(s.momentum_3m, 1)}</td>
                    <td className="px-3 py-2 text-right font-mono text-slate-300">{fmt(s.momentum_6m, 1)}</td>
                    <td className="px-3 py-2 text-right font-mono text-slate-300">{fmt(s.forward_pe)}</td>
                    <td className="px-3 py-2 text-right font-mono text-slate-300">{fmt(s.roe, 1)}</td>
                    <td className="px-3 py-2 text-right font-mono text-slate-300">{fmt(s.rsi_14, 0)}</td>
                    <td className="px-3 py-2 text-right font-mono">
                      <span className="text-amber-300">{fmt(s.breakout_score, 0)}</span>
                      <span className="text-slate-600 text-[10px]">/100</span>
                    </td>
                    <td className="px-5 py-2 text-right">
                      <span
                        className={`px-2 py-0.5 rounded text-xs ${
                          s.signal === "strong"
                            ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/40"
                            : s.signal === "moderate"
                            ? "bg-amber-500/10 text-amber-300 border border-amber-500/30"
                            : "bg-slate-700/30 text-slate-400 border border-slate-600/40"
                        }`}
                      >
                        {s.signal || "—"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <footer className="text-center text-xs text-slate-500 py-4">
          Data via yfinance · Forecasts via time-series linear regression · For research/educational use only
        </footer>
          </>
        )}
      </main>
    </div>
  )
}
