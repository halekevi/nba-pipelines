"use client"

import { useEffect, useState } from "react"

interface PipelineStatus {
  sport: string
  label: string
  status: "live" | "stale" | "offline"
  lastRun: string
  rows: number
  color: string
}

const DEFAULT_PIPELINES: PipelineStatus[] = [
  { sport: "NBA", label: "NBA Full Game", status: "live", lastRun: "2 min ago", rows: 6227, color: "#f0a500" },
  { sport: "NBA1H", label: "NBA 1st Half", status: "live", lastRun: "2 min ago", rows: 1842, color: "#ffb27d" },
  { sport: "NBA1Q", label: "NBA 1st Qtr", status: "live", lastRun: "2 min ago", rows: 1156, color: "#ffd87a" },
  { sport: "CBB", label: "College BB", status: "live", lastRun: "8 min ago", rows: 3421, color: "#00e5ff" },
  { sport: "NHL", label: "Hockey", status: "live", lastRun: "5 min ago", rows: 892, color: "#c4a5ff" },
  { sport: "MLB", label: "Baseball", status: "stale", lastRun: "3 hrs ago", rows: 2145, color: "#ff9a9a" },
  { sport: "SOCCER", label: "Soccer", status: "live", lastRun: "12 min ago", rows: 1567, color: "#e8b84a" },
]

interface PipelineTickerProps {
  pipelines?: PipelineStatus[]
  isLoading?: boolean
}

export function PipelineTicker({ pipelines, isLoading }: PipelineTickerProps) {
  const [offset, setOffset] = useState(0)
  const data = pipelines || DEFAULT_PIPELINES

  useEffect(() => {
    const interval = setInterval(() => {
      setOffset((prev) => (prev + 1) % 100)
    }, 50)
    return () => clearInterval(interval)
  }, [])

  const statusBadge = (status: string) => {
    if (status === "live") {
      return (
        <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-status-green">
          <span className="h-1.5 w-1.5 rounded-full bg-status-green animate-blink shadow-[0_0_8px_#39ff6e]" />
          LIVE
        </span>
      )
    }
    if (status === "stale") {
      return (
        <span className="text-[10px] font-semibold uppercase tracking-wider text-status-amber">
          STALE
        </span>
      )
    }
    return (
      <span className="text-[10px] font-semibold uppercase tracking-wider text-status-red">
        OFFLINE
      </span>
    )
  }

  if (isLoading) {
    return (
      <div className="glass-card py-3 px-4 animate-pulse">
        <div className="flex items-center gap-4">
          <div className="h-6 w-32 bg-glass-hi rounded" />
          <div className="flex-1 flex gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-14 w-48 bg-glass-hi rounded-xl" />
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="relative overflow-hidden glass-card py-3 px-0">
      <div className="absolute left-4 top-1/2 -translate-y-1/2 z-10 flex items-center gap-2 bg-background/80 backdrop-blur-sm pr-4">
        <span className="font-display text-lg tracking-wider text-gold">PIPELINE STATUS</span>
        <span className="h-4 w-px bg-glass-border" />
      </div>
      
      <div 
        className="flex gap-8 pl-48 whitespace-nowrap"
        style={{ transform: `translateX(-${offset * 2}px)` }}
      >
        {[...data, ...data].map((pipeline, idx) => (
          <div
            key={`${pipeline.sport}-${idx}`}
            className="flex items-center gap-4 px-4 py-2 rounded-xl bg-glass hover:bg-glass-mid transition-colors"
          >
            <div
              className="flex items-center justify-center w-10 h-10 rounded-lg text-sm font-display tracking-wide"
              style={{
                background: `${pipeline.color}18`,
                border: `1px solid ${pipeline.color}44`,
                color: pipeline.color,
              }}
            >
              {pipeline.sport}
            </div>
            <div className="flex flex-col">
              <span className="text-xs text-white/90 font-medium">{pipeline.label}</span>
              <div className="flex items-center gap-3 mt-0.5">
                {statusBadge(pipeline.status)}
                <span className="text-[10px] text-muted">{pipeline.lastRun}</span>
              </div>
            </div>
            <div className="pl-3 border-l border-glass-border">
              <span className="font-mono text-sm text-white/80">{pipeline.rows.toLocaleString()}</span>
              <span className="text-[10px] text-muted ml-1">rows</span>
            </div>
          </div>
        ))}
      </div>

      <div className="absolute right-0 top-0 bottom-0 w-24 bg-gradient-to-l from-background to-transparent pointer-events-none" />
    </div>
  )
}
