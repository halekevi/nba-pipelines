"use client"

interface Edge {
  player: string
  team: string
  opp: string
  prop: string
  line: number
  direction: string
  edge: number
  hitRate: number
  sport: string
}

interface L5Record {
  player: string
  team: string
  prop: string
  l5Hits: number
  direction: string
  avg: number
}

interface PowerRank {
  rank: number
  player: string
  team: string
  score: number
  trend: "up" | "down" | "same"
}

interface InsightsGridProps {
  insights?: {
    top_edges: Edge[]
    l5_streaks: L5Record[]
    power_ranks: PowerRank[]
  }
  isLoading?: boolean
}

const DEFAULT_EDGES: Edge[] = [
  { player: "Sandro Mamukelashvili", team: "TOR", opp: "MEM", prop: "3-PT Made", line: 0.5, direction: "OVER", edge: 1.75, hitRate: 1.0, sport: "NBA" },
  { player: "Ziaire Williams", team: "BKN", opp: "ATL", prop: "Steals", line: 0.5, direction: "OVER", edge: 1.95, hitRate: 1.0, sport: "NBA" },
  { player: "Josh Hart", team: "NYK", opp: "CHI", prop: "3-PT Made", line: 0.5, direction: "OVER", edge: 1.35, hitRate: 0.8, sport: "NBA" },
  { player: "Cooper Flagg", team: "DAL", opp: "ORL", prop: "Steals", line: 0.5, direction: "OVER", edge: 1.35, hitRate: 0.8, sport: "NBA" },
]

const DEFAULT_L5: L5Record[] = [
  { player: "S. Mamukelashvili", team: "TOR", prop: "3PM", l5Hits: 5, direction: "OVER", avg: 2.6 },
  { player: "Z. Williams", team: "BKN", prop: "STL", l5Hits: 5, direction: "OVER", avg: 2.8 },
  { player: "J. Hart", team: "NYK", prop: "3PM", l5Hits: 4, direction: "OVER", avg: 1.8 },
  { player: "C. Flagg", team: "DAL", prop: "STL", l5Hits: 4, direction: "OVER", avg: 2.2 },
  { player: "J. Fears", team: "NOP", prop: "3PM", l5Hits: 4, direction: "OVER", avg: 1.6 },
]

const DEFAULT_RANKS: PowerRank[] = [
  { rank: 1, player: "Sandro Mamukelashvili", team: "TOR", score: 2.35, trend: "up" },
  { rank: 2, player: "Ziaire Williams", team: "BKN", score: 2.22, trend: "same" },
  { rank: 3, player: "Cooper Flagg", team: "DAL", score: 2.17, trend: "up" },
  { rank: 4, player: "Josh Hart", team: "NYK", score: 2.05, trend: "down" },
  { rank: 5, player: "Jeremiah Fears", team: "NOP", score: 1.96, trend: "same" },
]

function EdgeCard({ edge }: { edge: Edge }) {
  return (
    <div className="glass-card glass-card-hover p-4 transition-all duration-200">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h4 className="text-sm font-medium text-white/95">{edge.player}</h4>
          <p className="text-xs text-muted mt-0.5">{edge.team} vs {edge.opp}</p>
        </div>
        <span 
          className="sport-pill"
          style={{
            background: "rgba(240, 165, 0, 0.12)",
            border: "1px solid rgba(240, 165, 0, 0.35)",
            color: "#f0a500",
          }}
        >
          {edge.sport}
        </span>
      </div>
      
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs text-white/80">{edge.prop}</span>
        <span className="text-xs font-mono text-white/90">{edge.line}</span>
        <span className={`text-xs font-bold ${edge.direction === "OVER" ? "text-status-cyan" : "text-gold"}`}>
          {edge.direction}
        </span>
      </div>
      
      <div className="flex items-center justify-between pt-3 border-t border-glass-border">
        <div className="flex flex-col">
          <span className="text-[10px] uppercase tracking-wider text-muted">Edge</span>
          <span className="text-lg font-display text-status-green">+{edge.edge.toFixed(2)}</span>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[10px] uppercase tracking-wider text-muted">Hit Rate</span>
          <span className="text-lg font-display text-white/90">{(edge.hitRate * 100).toFixed(0)}%</span>
        </div>
      </div>
    </div>
  )
}

function L5Heatmap({ streaks }: { streaks: L5Record[] }) {
  return (
    <div className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-display text-xl tracking-wider text-gold">L5 STREAKS</h3>
        <span className="text-[10px] uppercase tracking-wider text-muted">5/5 Heatmap</span>
      </div>
      
      <div className="space-y-2">
        {streaks.map((streak, idx) => (
          <div 
            key={idx}
            className="flex items-center gap-3 p-3 rounded-lg bg-glass hover:bg-glass-mid transition-colors"
          >
            <div className="flex gap-1">
              {[...Array(5)].map((_, i) => (
                <div
                  key={i}
                  className={`w-3 h-3 rounded-sm ${
                    i < streak.l5Hits 
                      ? "bg-status-green shadow-[0_0_6px_#39ff6e]" 
                      : "bg-status-red/60"
                  }`}
                />
              ))}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-white/90 truncate">{streak.player}</span>
                <span className="text-[10px] text-muted">{streak.team}</span>
              </div>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="text-[10px] text-white/70">{streak.prop}</span>
                <span className={`text-[10px] font-bold ${streak.direction === "OVER" ? "text-status-cyan" : "text-gold"}`}>
                  {streak.direction}
                </span>
              </div>
            </div>
            <div className="text-right">
              <span className="text-xs font-mono text-white/80">{streak.avg}</span>
              <span className="text-[10px] text-muted ml-1">avg</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function PowerRanks({ ranks }: { ranks: PowerRank[] }) {
  const trendIcon = (trend: "up" | "down" | "same") => {
    if (trend === "up") return <span className="text-status-green">&#9650;</span>
    if (trend === "down") return <span className="text-status-red">&#9660;</span>
    return <span className="text-muted">&#8212;</span>
  }

  return (
    <div className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-display text-xl tracking-wider text-gold">L10 POWER RANKS</h3>
        <span className="text-[10px] uppercase tracking-wider text-muted">Top 5</span>
      </div>
      
      <div className="space-y-2">
        {ranks.map((rank) => (
          <div 
            key={rank.rank}
            className="flex items-center gap-3 p-3 rounded-lg bg-glass hover:bg-glass-mid transition-colors"
          >
            <div className="w-8 h-8 rounded-lg bg-gold/10 border border-gold/30 flex items-center justify-center font-display text-gold text-lg">
              {rank.rank}
            </div>
            <div className="flex-1 min-w-0">
              <span className="text-xs font-medium text-white/90 truncate block">{rank.player}</span>
              <span className="text-[10px] text-muted">{rank.team}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-mono text-white/90">{rank.score.toFixed(2)}</span>
              {trendIcon(rank.trend)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-6 w-32 bg-glass-hi rounded" />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="space-y-3">
          {[1, 2].map((i) => (
            <div key={i} className="h-40 bg-glass-hi rounded-2xl" />
          ))}
        </div>
        <div className="h-64 bg-glass-hi rounded-2xl" />
        <div className="h-64 bg-glass-hi rounded-2xl" />
      </div>
    </div>
  )
}

export function InsightsGrid({ insights, isLoading }: InsightsGridProps) {
  const topEdges = insights?.top_edges || DEFAULT_EDGES
  const l5Streaks = insights?.l5_streaks || DEFAULT_L5
  const powerRanks = insights?.power_ranks || DEFAULT_RANKS

  if (isLoading) {
    return <LoadingSkeleton />
  }

  return (
    <section className="mb-8">
      <div className="flex items-center gap-3 mb-5">
        <h2 className="font-display text-2xl tracking-wider text-gold">INSIGHTS</h2>
        <div className="flex-1 h-px bg-gradient-to-r from-gold/30 to-transparent" />
      </div>
      
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Top Edges */}
        <div className="lg:col-span-1">
          <h3 className="font-display text-lg tracking-wider text-white/80 mb-3">TOP EDGES</h3>
          <div className="grid gap-3">
            {topEdges.slice(0, 2).map((edge, idx) => (
              <EdgeCard key={idx} edge={edge} />
            ))}
          </div>
        </div>

        {/* L5 Heatmap */}
        <div className="lg:col-span-1">
          <L5Heatmap streaks={l5Streaks.slice(0, 5)} />
        </div>

        {/* Power Ranks */}
        <div className="lg:col-span-1">
          <PowerRanks ranks={powerRanks.slice(0, 5)} />
        </div>
      </div>
    </section>
  )
}
