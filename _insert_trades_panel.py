#!/usr/bin/env python3
"""Insert the Recent Agent Trades panel into AgentPanel.jsx"""

jsx = open('frontend/src/components/AgentPanel.jsx').read()

marker = '      {/* Pending proposals (SEMI_AUTO) */}'
idx = jsx.find(marker)
if idx == -1:
    print("ERROR: marker not found")
    exit(1)

panel = '''      {trades && (
        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-3">
            <TrendingUp className="w-4 h-4 text-emerald-300" /> Recent Agent Trades
          </div>
          {trades.stats && (
            <div className="text-xs text-slate-400 space-y-1 mb-3 pb-2 border-b border-slate-800">
              <div>
                <span className="text-slate-500">Win rate:</span>
                <span className={`font-mono ${trades.stats.win_rate_pct >= 50 ? "text-emerald-300" : "text-rose-300"}`}>
                  {trades.stats.win_rate_pct.toFixed(1)}%
                </span>
                {" "}({trades.stats.wins}W - {trades.stats.losses}L · {trades.stats.count} trades)
              </div>
              <div>
                <span className="text-slate-500">Total P&L:</span>
                <span className={`font-mono ${(trades.stats.total_pnl_eur || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                  €{(trades.stats.total_pnl_eur || 0).toFixed(2)}
                </span>
                {" "}· Avg hold: {trades.stats.avg_hold_days.toFixed(1)}d
              </div>
            </div>
          )}
          {trades.trades?.length > 0 && (
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {trades.trades.map((t) => (
                <div key={t.id} className="flex items-center justify-between text-xs">
                  <span className="font-mono font-semibold">{t.symbol}</span>
                  <span className={`px-1.5 py-0.25 rounded ${
                    (t.pnl_eur || 0) > 0
                      ? "bg-emerald-500/10 text-emerald-300"
                      : "bg-rose-500/10 text-rose-300"
                  }`}>
                    €{(t.pnl_eur || 0).toFixed(2)} ({(t.pnl_pct || 0).toFixed(1)}%)
                  </span>
                  <span className="text-slate-500">
                    {t.exit_reason} · {t.hold_days?.toFixed(1)}d
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

'''

new_jsx = jsx[:idx] + panel + jsx[idx:]
open('frontend/src/components/AgentPanel.jsx', 'w').write(new_jsx)
print("INSERTED OK")
