"use client"

import useSWR from "swr"

const fetcher = (url: string) => fetch(url).then((res) => res.json())

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

export function useDashboardData() {
  const { data, error, isLoading, mutate } = useSWR<DashboardData>(
    "/api/dashboard",
    fetcher,
    {
      refreshInterval: 60000, // Refresh every 60 seconds
      revalidateOnFocus: true,
    }
  )

  return {
    data,
    error,
    isLoading,
    refresh: mutate,
  }
}
