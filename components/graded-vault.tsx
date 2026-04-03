"use client"

interface GradedTicket {
  id: string
  date: string
  groupName: string
  legs: number
  hits: number
  misses: number
  status: "hit" | "miss" | "partial"
  payout: number
  ev: number
}

interface GradedVaultProps {
  history?: GradedTicket[]
  isLoading?: boolean
}

const DEFAULT_HISTORY: GradedTicket[] = [
  { id: "V001", date: "2026-04-02", groupName: "NBA Power Play 3-Leg", legs: 3, hits: 3, misses: 0, status: "hit", payout: 6.0, ev: 3.68 },
  { id: "V002", date: "2026-04-02", groupName: "NBA Goblin 2-Leg", legs: 2, hits: 2, misses: 0, status: "hit", payout: 3.0, ev: 2.17 },
  { id: "V003", date: "2026-04-02", groupName: "NBA Flex 4-Leg", legs: 4, hits: 2, misses: 2, status: "miss", payout: 0, ev: 5.22 },
  { id: "V004", date: "2026-04-01", groupName: "NHL Standard 3-Leg", legs: 3, hits: 3, misses: 0, status: "hit", payout: 6.0, ev: 3.45 },
  { id: "V005", date: "2026-04-01", groupName: "CBB Power Play 2-Leg", legs: 2, hits: 1, misses: 1, status: "miss", payout: 0, ev: 2.85 },
  { id: "V006", date: "2026-04-01", groupName: "NBA Standard 2-Leg", legs: 2, hits: 2, misses: 0, status: "hit", payout: 3.0, ev: 2.17 },
  { id: "V007", date: "2026-03-31", groupName: "NBA Flex 3-Leg", legs: 3, hits: 2, misses: 1, status: "partial", payout: 1.5, ev: 3.68 },
  { id: "V008", date: "2026-03-31", groupName: "MLB Standard 3-Leg", legs: 3, hits: 0, misses: 3, status: "miss", payout: 0, ev: 3.22 },
]

function VaultStats({ history }: { history: GradedTicket[] }) {
  const totalWins = history.filter((t) => t.status === "hit").length
  const totalLosses = history.filter((t) => t.status === "miss").length
  const winRate = history.length > 0 ? (totalWins / history.length) * 100 : 0
  const totalPayout = history.reduce((sum, t) => sum + t.payout, 0)

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
      <div className="glass-card p-4 text-center">
        <div className="text-2xl font-display text-status-green">{totalWins}</div>
        <div className="text-[10px] uppercase tracking-wider text-muted mt-1">Perfect Hits</div>
      </div>
      <div className="glass-card p-4 text-center">
        <div className="text-2xl font-display text-status-red">{totalLosses}</div>
        <div className="text-[10px] uppercase tracking-wider text-muted mt-1">Misses</div>
      </div>
      <div className="glass-card p-4 text-center">
        <div className="text-2xl font-display text-white/90">{winRate.toFixed(0)}%</div>
        <div className="text-[10px] uppercase tracking-wider text-muted mt-1">Win Rate</div>
      </div>
      <div className="glass-card p-4 text-center">
        <div className="text-2xl font-display text-gold">{totalPayout.toFixed(1)}x</div>
        <div className="text-[10px] uppercase tracking-wider text-muted mt-1">Total Payout</div>
      </div>
    </div>
  )
}

function VaultRow({ ticket }: { ticket: GradedTicket }) {
  const statusStyles = {
    hit: "bg-status-green/10 border-status-green/40 text-status-green",
    miss: "bg-status-red/10 border-status-red/40 text-status-red",
    partial: "bg-gold/10 border-gold/40 text-gold",
  }

  const statusLabel = {
    hit: "HIT",
    miss: "MISS",
    partial: "FLEX",
  }

  const statusIcon = {
    hit: "\u2713",
    miss: "\u2717",
    partial: "~",
  }

  return (
    <div className="glass-card glass-card-hover p-4 flex flex-wrap items-center gap-4 transition-all duration-200">
      {/* Status indicator */}
      <div
        className={`w-12 h-12 rounded-xl flex items-center justify-center font-display text-lg border ${statusStyles[ticket.status]}`}
      >
        {statusIcon[ticket.status]}
      </div>

      {/* Ticket info */}
      <div className="flex-1 min-w-[180px]">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-muted">{ticket.id}</span>
          <span className="text-[10px] text-muted/60">{ticket.date}</span>
        </div>
        <div className="text-sm text-white/90 mt-1">{ticket.groupName}</div>
      </div>

      {/* Leg breakdown */}
      <div className="flex items-center gap-2">
        <span className="text-sm font-mono text-status-green">{ticket.hits}&#10003;</span>
        <span className="text-sm font-mono text-status-red">{ticket.misses}&#10007;</span>
        <span className="text-xs text-muted">/ {ticket.legs}</span>
      </div>

      {/* Payout */}
      <div className="text-right min-w-[80px]">
        <div className={`text-lg font-display ${ticket.payout > 0 ? "text-status-green" : "text-muted"}`}>
          {ticket.payout > 0 ? `+${ticket.payout.toFixed(1)}x` : "0x"}
        </div>
        <div className="text-[10px] text-muted">EV: {ticket.ev.toFixed(2)}</div>
      </div>

      {/* Status badge */}
      <span
        className={`font-display text-xs tracking-wider px-4 py-1.5 rounded-full border ${statusStyles[ticket.status]}`}
      >
        {statusLabel[ticket.status]}
      </span>
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div className="animate-pulse space-y-4">
      <div className="flex items-center gap-3">
        <div className="h-7 w-32 bg-glass-hi rounded" />
        <div className="flex-1 h-px bg-glass-hi" />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-20 bg-glass-hi rounded-2xl" />
        ))}
      </div>
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="h-20 bg-glass-hi rounded-2xl" />
      ))}
    </div>
  )
}

export function GradedVault({ history, isLoading }: GradedVaultProps) {
  const data = history || DEFAULT_HISTORY

  if (isLoading) {
    return <LoadingSkeleton />
  }

  return (
    <section>
      <div className="flex items-center gap-3 mb-5">
        <h2 className="font-display text-2xl tracking-wider text-gold">THE VAULT</h2>
        <span className="text-[10px] uppercase tracking-wider text-muted">Graded History</span>
        <div className="flex-1 h-px bg-gradient-to-r from-gold/30 to-transparent" />
      </div>

      <VaultStats history={data} />

      <div className="space-y-3 max-h-[500px] overflow-y-auto pr-1">
        {data.map((ticket) => (
          <VaultRow key={ticket.id} ticket={ticket} />
        ))}
      </div>
    </section>
  )
}
