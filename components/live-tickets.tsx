"use client"

interface Leg {
  sport: string
  player: string
  team: string
  opp: string
  prop_type: string
  pick_type: string
  direction: "OVER" | "UNDER"
  line: number
  edge: number
  hit_rate: number
  rank_score: number
  status?: "pending" | "hit" | "miss"
  actual?: number
}

interface Ticket {
  ticket_no: number
  avg_hit_rate: number
  power_payout: number
  flex_payout: number
  ev_power: number
  legs: Leg[]
  status?: "pending" | "hit" | "miss"
}

interface TicketGroup {
  group_name: string
  n_legs: number
  power_payout: number
  flex_payout: number
  tickets: Ticket[]
}

interface LiveTicketsProps {
  groups?: TicketGroup[]
  isLoading?: boolean
}

const DEFAULT_GROUPS: TicketGroup[] = [
  {
    group_name: "NBA Power Play 2-Leg",
    n_legs: 2,
    power_payout: 3.0,
    flex_payout: 3.0,
    tickets: [
      {
        ticket_no: 1,
        avg_hit_rate: 0.9,
        power_payout: 3.0,
        flex_payout: 3.0,
        ev_power: 2.17,
        legs: [
          { sport: "NBA", player: "Sandro Mamukelashvili", team: "TOR", opp: "MEM", prop_type: "3-PT Made", pick_type: "Goblin", direction: "OVER", line: 0.5, edge: 1.75, hit_rate: 1.0, rank_score: 2.35 },
          { sport: "NBA", player: "Josh Hart", team: "NYK", opp: "CHI", prop_type: "3-PT Made", pick_type: "Goblin", direction: "OVER", line: 0.5, edge: 1.35, hit_rate: 0.8, rank_score: 2.05 },
        ],
      },
    ],
  },
]

function getSportColor(sport: string) {
  const colors: Record<string, { bg: string; border: string; text: string }> = {
    NBA: { bg: "rgba(240, 165, 0, 0.12)", border: "rgba(240, 165, 0, 0.35)", text: "#f0a500" },
    NBA1H: { bg: "rgba(255, 155, 86, 0.12)", border: "rgba(255, 155, 86, 0.32)", text: "#ffb27d" },
    NBA1Q: { bg: "rgba(255, 214, 102, 0.12)", border: "rgba(255, 214, 102, 0.32)", text: "#ffd87a" },
    CBB: { bg: "rgba(0, 229, 255, 0.10)", border: "rgba(0, 229, 255, 0.32)", text: "#00e5ff" },
    NHL: { bg: "rgba(186, 130, 255, 0.12)", border: "rgba(186, 130, 255, 0.38)", text: "#c4a5ff" },
    MLB: { bg: "rgba(255, 121, 121, 0.12)", border: "rgba(255, 121, 121, 0.32)", text: "#ff9a9a" },
    Soccer: { bg: "rgba(240, 165, 0, 0.10)", border: "rgba(240, 165, 0, 0.34)", text: "#e8b84a" },
  }
  return colors[sport] || { bg: "rgba(255, 255, 255, 0.04)", border: "rgba(255, 255, 255, 0.1)", text: "#888" }
}

function getTierFromRankScore(score: number): string {
  if (score >= 2.2) return "S"
  if (score >= 2.0) return "A"
  if (score >= 1.8) return "B"
  return "C"
}

function StatusBadge({ status }: { status?: "pending" | "hit" | "miss" }) {
  if (status === "hit") {
    return (
      <span className="font-display text-xs tracking-wider px-4 py-1.5 rounded-full border bg-status-green/10 border-status-green/45 text-status-green shadow-[0_0_16px_rgba(57,255,110,0.15)]">
        HIT
      </span>
    )
  }
  if (status === "miss") {
    return (
      <span className="font-display text-xs tracking-wider px-4 py-1.5 rounded-full border bg-status-red/10 border-status-red/50 text-status-red shadow-[0_0_16px_rgba(255,77,77,0.12)]">
        MISS
      </span>
    )
  }
  return (
    <span className="font-display text-xs tracking-wider px-4 py-1.5 rounded-full border bg-glass border-glass-border text-muted">
      UNGRADED
    </span>
  )
}

function TicketCard({ ticket, groupName }: { ticket: Ticket; groupName: string }) {
  const hitCount = ticket.legs.filter((l) => l.status === "hit").length
  const missCount = ticket.legs.filter((l) => l.status === "miss").length

  return (
    <article className="glass-card overflow-hidden mb-5">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 px-5 py-4 bg-black/20 border-b border-glass-border">
        <span className="font-display text-2xl tracking-wider text-gold">#{ticket.ticket_no}</span>
        <span className="text-xs font-mono text-muted">{groupName}</span>
        <span className="text-xs font-mono text-muted">{hitCount}&#10003; {missCount}&#10007; / {ticket.legs.length}</span>
        <span className="text-xs font-mono text-status-cyan">PWR {ticket.power_payout}x | FLEX {ticket.flex_payout}x</span>
        <div className="ml-auto">
          <StatusBadge status={ticket.status} />
        </div>
      </div>

      {/* Legs */}
      <div className="divide-y divide-glass-border">
        {ticket.legs.map((leg, idx) => {
          const sportStyle = getSportColor(leg.sport)
          const tier = getTierFromRankScore(leg.rank_score)
          const status = leg.status || "pending"
          
          return (
            <div
              key={idx}
              className={`grid grid-cols-[48px_80px_1fr_40px_1fr_auto_auto_auto] gap-3 items-center px-5 py-3.5 text-sm ${
                status === "hit" 
                  ? "bg-status-green/[0.04] border-l-[3px] border-l-status-green" 
                  : status === "miss"
                  ? "bg-status-red/[0.10] border-l-[4px] border-l-status-red"
                  : "border-l-[3px] border-l-transparent"
              }`}
            >
              {/* Status Badge */}
              <div className="flex justify-center">
                {status === "hit" && (
                  <span className="text-2xl text-status-green">&#10003;</span>
                )}
                {status === "miss" && (
                  <span className="text-2xl text-status-red">&#10007;</span>
                )}
                {status === "pending" && (
                  <span className="text-lg text-muted">&#183;</span>
                )}
              </div>

              {/* Sport Pill */}
              <div>
                <span
                  className="sport-pill"
                  style={{
                    background: sportStyle.bg,
                    border: `1px solid ${sportStyle.border}`,
                    color: sportStyle.text,
                  }}
                >
                  {leg.sport}
                </span>
              </div>

              {/* Player */}
              <div className={status === "pending" ? "text-muted" : status === "hit" ? "text-status-green" : "text-status-red"}>
                {leg.player}
              </div>

              {/* Tier */}
              <div className="flex justify-center">
                <span className="w-8 h-8 rounded-lg bg-glass border border-glass-border flex items-center justify-center font-display text-gold text-sm">
                  {tier}
                </span>
              </div>

              {/* Prop + Matchup */}
              <div>
                <div className="text-white/90">{leg.prop_type}</div>
                <div className="text-xs text-muted mt-0.5">{leg.team} vs {leg.opp}</div>
              </div>

              {/* Line + Direction */}
              <div className="font-mono text-sm whitespace-nowrap">
                {leg.line}{" "}
                <span className={leg.direction === "OVER" ? "text-status-cyan font-bold" : "text-gold font-bold"}>
                  {leg.direction}
                </span>
              </div>

              {/* Actual */}
              <div className={`font-mono text-sm ${
                status === "pending" ? "text-muted" : status === "hit" ? "text-status-green" : "text-status-red"
              }`}>
                {leg.actual !== undefined ? leg.actual : "—"}
              </div>

              {/* Edge */}
              <div className="font-mono text-sm text-white/80">
                {leg.edge.toFixed(2)}
              </div>
            </div>
          )
        })}
      </div>
    </article>
  )
}

function LoadingSkeleton() {
  return (
    <div className="animate-pulse space-y-4">
      <div className="flex items-center gap-3">
        <div className="h-7 w-40 bg-glass-hi rounded" />
        <div className="flex-1 h-px bg-glass-hi" />
      </div>
      {[1, 2, 3].map((i) => (
        <div key={i} className="h-48 bg-glass-hi rounded-2xl" />
      ))}
    </div>
  )
}

export function LiveTickets({ groups, isLoading }: LiveTicketsProps) {
  const data = groups || DEFAULT_GROUPS
  
  // Flatten all tickets with their group info
  const allTickets = data.flatMap((group) =>
    group.tickets.map((ticket) => ({ ticket, groupName: group.group_name }))
  )

  if (isLoading) {
    return <LoadingSkeleton />
  }

  return (
    <section className="mb-8">
      <div className="flex items-center gap-3 mb-5">
        <h2 className="font-display text-2xl tracking-wider text-gold">LIVE TICKETS</h2>
        <div className="flex-1 h-px bg-gradient-to-r from-gold/30 to-transparent" />
        <span className="text-xs font-mono text-muted">{allTickets.length} active</span>
      </div>
      
      <div className="max-h-[600px] overflow-y-auto pr-1 space-y-0">
        {allTickets.map(({ ticket, groupName }, idx) => (
          <TicketCard key={idx} ticket={ticket} groupName={groupName} />
        ))}
      </div>
    </section>
  )
}
