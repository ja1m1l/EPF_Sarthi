import { useState } from 'react';

import type { EvidenceCheck, EvidenceReport, EvidenceVerdict } from '@/api/types';
import { surface } from '@/components/ui';
import { cn } from '@/lib/utils';

/**
 * NOT_FOUND and CONTRADICTED are different findings — "we did not see it"
 * versus "the document says otherwise" — so they are styled and worded
 * distinctly and never merged into a single "failed" look.
 */
const VERDICT: Record<EvidenceVerdict, { label: string; badge: string; row: string }> = {
  CONFIRMED: {
    label: 'Confirmed',
    badge: 'bg-emerald-100 text-emerald-800 ring-1 ring-emerald-300',
    row: '',
  },
  NOT_FOUND: {
    label: 'Not found',
    badge: 'bg-slate-100 text-slate-600 ring-1 ring-slate-300',
    row: '',
  },
  CONTRADICTED: {
    label: 'Contradicted',
    badge: 'bg-red-100 text-red-800 ring-1 ring-red-400',
    row: 'bg-red-50',
  },
};

export function EvidenceTable({ report }: { report: EvidenceReport | undefined }) {
  if (!report) return null;

  return (
    <section className={cn(surface, 'p-5')}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        What your documents confirmed
      </h2>

      {report.summary && <p className="mt-2 text-sm text-neutral-600">{report.summary}</p>}

      <ul className="mt-4 divide-y divide-neutral-200/80 border-y border-neutral-200/80">
        {report.checks.map((check) => (
          <EvidenceRow key={check.checkId} check={check} />
        ))}
      </ul>

      <p className="mt-3 text-xs text-neutral-500">
        A check is only marked confirmed when the excerpt it relies on appears verbatim in the
        uploaded document. Unsupported confirmations are downgraded automatically.
      </p>
    </section>
  );
}

function EvidenceRow({ check }: { check: EvidenceCheck }) {
  const [expanded, setExpanded] = useState(false);
  const style = VERDICT[check.verdict];
  const excerpt = check.sourceRef?.excerpt;

  return (
    <li className={`py-3 ${style.row}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-medium text-neutral-900">{check.label}</p>
          {check.note && <p className="mt-0.5 text-sm text-neutral-600">{check.note}</p>}

          {excerpt && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="mt-1 text-xs text-sky-700 underline underline-offset-2"
            >
              {expanded ? 'Hide' : 'Show'} the excerpt this relied on
            </button>
          )}
        </div>

        <span
          className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${style.badge}`}
        >
          {style.label}
        </span>
      </div>

      {expanded && excerpt && (
        <figure className="mt-2 rounded-md bg-white/80 p-3">
          <blockquote className="font-mono text-xs leading-relaxed text-neutral-800">
            {excerpt}
          </blockquote>
          <figcaption className="mt-2 text-xs text-neutral-500">
            From document {check.sourceRef?.documentId}
          </figcaption>
        </figure>
      )}
    </li>
  );
}
