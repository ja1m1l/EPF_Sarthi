import type { SlaResult, SlaStatus } from '@/api/types';
import { surface } from '@/components/ui';
import { formatDate } from '@/format';
import { cn } from '@/lib/utils';

const TRAFFIC_LIGHT: Record<SlaStatus, { dot: string; card: string; label: string }> = {
  WITHIN: {
    dot: 'bg-emerald-500',
    card: 'border-emerald-300/80 bg-emerald-50/85',
    label: 'Within the published timeline',
  },
  APPROACHING: {
    dot: 'bg-amber-500',
    card: 'border-amber-300/80 bg-amber-50/85',
    label: 'Approaching the deadline',
  },
  OVERDUE: {
    dot: 'bg-red-500',
    card: 'border-red-300/80 bg-red-50/85',
    label: 'Published timeline appears exceeded',
  },
};

export function SlaCard({ sla }: { sla: SlaResult | undefined }) {
  if (!sla) return null;

  // No timeline was established, so there is nothing to count against. We say
  // so rather than showing a green light, which would imply the claim is fine.
  if (sla.skipped) {
    return (
      <section className={cn(surface, 'p-5')}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
          Timeline status
        </h2>
        <p className="mt-3 flex items-center gap-2 text-lg font-medium text-neutral-700">
          <span className="h-3 w-3 rounded-full bg-neutral-400" />
          Not computed
        </p>
        <p className="mt-2 text-sm text-neutral-600">
          No deadline was calculated because no applicable published rule was matched for this
          claim.
        </p>
      </section>
    );
  }

  const light = TRAFFIC_LIGHT[sla.status];

  return (
    <section className={cn('rounded-xl border p-5 backdrop-blur-md', light.card)}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Timeline status
      </h2>

      <p className="mt-3 flex items-center gap-2 text-lg font-medium text-neutral-900">
        <span className={`h-3 w-3 rounded-full ${light.dot}`} />
        {light.label}
      </p>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
        <Stat label="Clock started" value={formatDate(sla.clockStartDateIso)} />
        <Stat label="Deadline" value={formatDate(sla.deadlineDateIso)} />
        <Stat label="Days elapsed" value={String(sla.elapsedDays)} />
        <Stat
          label="Days remaining"
          value={sla.remainingDays < 0 ? `${sla.remainingDays} (past due)` : String(sla.remainingDays)}
        />
      </dl>

      {/*
        Reported separately and never folded into elapsedDays: time before a
        deficiency was raised is a different quantity from time on the
        current clock.
      */}
      {sla.priorElapsedDays !== null && sla.priorElapsedDays !== undefined && (
        <p className="mt-3 text-sm text-neutral-700">
          <span className="font-medium">{sla.priorElapsedDays} days</span> elapsed before the
          deficiency was raised, counted separately from the {sla.elapsedDays} days above.
        </p>
      )}

      <p className="mt-4 border-t border-black/10 pt-3 text-sm leading-relaxed text-neutral-700">
        {sla.explanation}
      </p>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className="mt-0.5 font-medium text-neutral-900">{value}</dd>
    </div>
  );
}
