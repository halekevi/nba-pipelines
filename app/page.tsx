"use client"

import { Header } from "@/components/header"
import { PipelineTicker } from "@/components/pipeline-ticker"
import { InsightsGrid } from "@/components/insights-grid"
import { LiveTickets } from "@/components/live-tickets"
import { GradedVault } from "@/components/graded-vault"
import { useDashboardData } from "@/hooks/use-dashboard-data"

export default function CommandCenter() {
  const { data, isLoading } = useDashboardData()

  return (
    <main className="relative z-10 min-h-screen">
      <div className="w-full max-w-[1920px] mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Header */}
        <Header lastSync={data?.generated_at} />

        {/* Section 1: Pipeline Slates Ticker */}
        <section className="mb-8">
          <PipelineTicker pipelines={data?.pipeline_status} isLoading={isLoading} />
        </section>

        {/* Section 2: Insights Grid */}
        <InsightsGrid insights={data?.insights} isLoading={isLoading} />

        {/* Section 3 & 4: Split layout for Tickets and Vault */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {/* Live Tickets */}
          <LiveTickets groups={data?.groups} isLoading={isLoading} />

          {/* Graded Vault */}
          <GradedVault history={data?.graded_history} isLoading={isLoading} />
        </div>
      </div>
    </main>
  )
}
