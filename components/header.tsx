"use client"

import { useState, useEffect } from "react"

interface HeaderProps {
  lastSync?: string
}

export function Header({ lastSync }: HeaderProps) {
  const [currentDate, setCurrentDate] = useState<string>("")
  const [syncText, setSyncText] = useState<string>("Syncing...")

  useEffect(() => {
    // Set date only on client to avoid hydration mismatch
    setCurrentDate(
      new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
    )

    // Calculate last sync time
    if (lastSync) {
      const date = new Date(lastSync)
      const now = new Date()
      const diffMs = now.getTime() - date.getTime()
      const diffMins = Math.floor(diffMs / 60000)
      if (diffMins < 1) {
        setSyncText("Just now")
      } else if (diffMins < 60) {
        setSyncText(`${diffMins} min ago`)
      } else {
        setSyncText(`${Math.floor(diffMins / 60)} hrs ago`)
      }
    }
  }, [lastSync])

  return (
    <header className="glass-card px-6 py-4 mb-6 flex items-center justify-between gap-4 flex-wrap">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-gold/10 border border-gold/30 flex items-center justify-center">
            <svg className="w-6 h-6 text-gold" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5m.75-9l3-3 2.148 2.148A12.061 12.061 0 0116.5 7.605" />
            </svg>
          </div>
          <div>
            <h1 className="font-display text-2xl tracking-wider">
              <span className="text-white/90">prop</span>
              <span className="text-gold">ORACLE</span>
            </h1>
            <p className="text-[10px] uppercase tracking-[0.2em] text-muted">Command Center</p>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-status-green/10 border border-status-green/30">
          <span className="w-2 h-2 rounded-full bg-status-green animate-blink shadow-[0_0_8px_#39ff6e]" />
          <span className="text-xs font-semibold tracking-wider text-status-green uppercase">Live</span>
        </div>

        <div className="text-right">
          <p className="text-xs font-mono text-white/80">
            {currentDate || "Loading..."}
          </p>
          <p className="text-[10px] text-muted">Last sync: {syncText}</p>
        </div>
      </div>
    </header>
  )
}
