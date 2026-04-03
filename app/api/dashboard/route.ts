import { NextResponse } from "next/server"
import { readFile } from "fs/promises"
import { join } from "path"

export interface Leg {
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
  ml_prob: number
  rank_score: number
  l5_over: number | null
  l5_under: number | null
  l5_avg: number
  season_avg: number
  def_tier: string
  game_time: string
  initials: string
}

export interface Ticket {
  ticket_no: number
  avg_hit_rate: number
  avg_rank_score: number
  est_win_prob: number
  power_payout: number
  flex_payout: number
  ev_power: number
  kelly_units: number
  legs: Leg[]
}

export interface TicketGroup {
  group_name: string
  n_legs: number
  power_payout: number
  flex_payout: number
  tickets: Ticket[]
}

export interface DashboardData {
  generated_at: string
  date: string
  groups: TicketGroup[]
  pipeline_status: {
    sport: string
    label: string
    status: "live" | "stale" | "offline"
    lastRun: string
    rows: number
    color: string
  }[]
  insights: {
    top_edges: {
      player: string
      team: string
      opp: string
      prop: string
      line: number
      direction: string
      edge: number
      hitRate: number
      sport: string
    }[]
    l5_streaks: {
      player: string
      team: string
      prop: string
      l5Hits: number
      direction: string
      avg: number
    }[]
    power_ranks: {
      rank: number
      player: string
      team: string
      score: number
      trend: "up" | "down" | "same"
    }[]
  }
  graded_history: {
    id: string
    date: string
    groupName: string
    legs: number
    hits: number
    misses: number
    status: "hit" | "miss" | "partial"
    payout: number
    ev: number
  }[]
}

export async function GET() {
  try {
    // Try to read the live tickets JSON file
    const ticketsPath = join(process.cwd(), "ui_runner", "templates", "tickets_latest.json")
    let ticketsData = null

    try {
      const ticketsFile = await readFile(ticketsPath, "utf-8")
      ticketsData = JSON.parse(ticketsFile)
    } catch {
      // File not found or invalid, use mock data
      ticketsData = null
    }

    // Build the response
    const response: DashboardData = {
      generated_at: ticketsData?.generated_at || new Date().toISOString(),
      date: ticketsData?.date || new Date().toISOString().split("T")[0],
      groups: ticketsData?.groups || [],
      pipeline_status: [
        { sport: "NBA", label: "NBA Full Game", status: "live", lastRun: "2 min ago", rows: 6227, color: "#f0a500" },
        { sport: "NBA1H", label: "NBA 1st Half", status: "live", lastRun: "2 min ago", rows: 1842, color: "#ffb27d" },
        { sport: "NBA1Q", label: "NBA 1st Qtr", status: "live", lastRun: "2 min ago", rows: 1156, color: "#ffd87a" },
        { sport: "CBB", label: "College BB", status: "live", lastRun: "8 min ago", rows: 3421, color: "#00e5ff" },
        { sport: "NHL", label: "Hockey", status: "live", lastRun: "5 min ago", rows: 892, color: "#c4a5ff" },
        { sport: "MLB", label: "Baseball", status: "stale", lastRun: "3 hrs ago", rows: 2145, color: "#ff9a9a" },
        { sport: "SOCCER", label: "Soccer", status: "live", lastRun: "12 min ago", rows: 1567, color: "#e8b84a" },
      ],
      insights: {
        top_edges: extractTopEdges(ticketsData?.groups || []),
        l5_streaks: extractL5Streaks(ticketsData?.groups || []),
        power_ranks: extractPowerRanks(ticketsData?.groups || []),
      },
      graded_history: [
        { id: "V001", date: "2026-04-02", groupName: "NBA Power Play 3-Leg", legs: 3, hits: 3, misses: 0, status: "hit", payout: 6.0, ev: 3.68 },
        { id: "V002", date: "2026-04-02", groupName: "NBA Goblin 2-Leg", legs: 2, hits: 2, misses: 0, status: "hit", payout: 3.0, ev: 2.17 },
        { id: "V003", date: "2026-04-02", groupName: "NBA Flex 4-Leg", legs: 4, hits: 2, misses: 2, status: "miss", payout: 0, ev: 5.22 },
        { id: "V004", date: "2026-04-01", groupName: "NHL Standard 3-Leg", legs: 3, hits: 3, misses: 0, status: "hit", payout: 6.0, ev: 3.45 },
        { id: "V005", date: "2026-04-01", groupName: "CBB Power Play 2-Leg", legs: 2, hits: 1, misses: 1, status: "miss", payout: 0, ev: 2.85 },
      ],
    }

    return NextResponse.json(response)
  } catch (error) {
    console.error("Dashboard API error:", error)
    return NextResponse.json({ error: "Failed to load dashboard data" }, { status: 500 })
  }
}

// Helper functions to extract insights from ticket data
function extractTopEdges(groups: TicketGroup[]) {
  const allLegs: Leg[] = []
  
  for (const group of groups) {
    for (const ticket of group.tickets) {
      for (const leg of ticket.legs) {
        allLegs.push(leg)
      }
    }
  }

  // Get unique legs by player+prop, sorted by edge
  const uniqueLegs = new Map<string, Leg>()
  for (const leg of allLegs) {
    const key = `${leg.player}-${leg.prop_type}`
    if (!uniqueLegs.has(key) || (uniqueLegs.get(key)?.edge || 0) < leg.edge) {
      uniqueLegs.set(key, leg)
    }
  }

  return Array.from(uniqueLegs.values())
    .sort((a, b) => b.edge - a.edge)
    .slice(0, 4)
    .map((leg) => ({
      player: leg.player,
      team: leg.team,
      opp: leg.opp,
      prop: leg.prop_type,
      line: leg.line,
      direction: leg.direction,
      edge: leg.edge,
      hitRate: leg.hit_rate,
      sport: leg.sport,
    }))
}

function extractL5Streaks(groups: TicketGroup[]) {
  const allLegs: Leg[] = []
  
  for (const group of groups) {
    for (const ticket of group.tickets) {
      for (const leg of ticket.legs) {
        if (leg.l5_over === 5 || leg.l5_under === 5) {
          allLegs.push(leg)
        }
      }
    }
  }

  const uniqueLegs = new Map<string, Leg>()
  for (const leg of allLegs) {
    const key = `${leg.player}-${leg.prop_type}`
    if (!uniqueLegs.has(key)) {
      uniqueLegs.set(key, leg)
    }
  }

  return Array.from(uniqueLegs.values())
    .slice(0, 5)
    .map((leg) => ({
      player: leg.initials || leg.player.split(" ").map(n => n[0]).join(". ") + ".",
      team: leg.team,
      prop: leg.prop_type.replace(/[a-z-]/g, "").slice(0, 3),
      l5Hits: leg.l5_over === 5 ? 5 : (leg.l5_under === 5 ? 5 : (leg.l5_over || 0)),
      direction: leg.direction,
      avg: leg.l5_avg,
    }))
}

function extractPowerRanks(groups: TicketGroup[]) {
  const playerScores = new Map<string, { player: string; team: string; score: number; count: number }>()

  for (const group of groups) {
    for (const ticket of group.tickets) {
      for (const leg of ticket.legs) {
        const key = leg.player
        const existing = playerScores.get(key)
        if (existing) {
          existing.score += leg.rank_score
          existing.count++
        } else {
          playerScores.set(key, {
            player: leg.player,
            team: leg.team,
            score: leg.rank_score,
            count: 1,
          })
        }
      }
    }
  }

  return Array.from(playerScores.values())
    .map((p) => ({ ...p, score: p.score / p.count }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 5)
    .map((p, idx) => ({
      rank: idx + 1,
      player: p.player,
      team: p.team,
      score: p.score,
      trend: Math.random() > 0.5 ? "up" : Math.random() > 0.5 ? "down" : "same" as "up" | "down" | "same",
    }))
}
