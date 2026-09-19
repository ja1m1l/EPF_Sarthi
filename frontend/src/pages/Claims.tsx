import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { listClaims, listRuns } from '@/api/client';
import type { AnalysisRun, AnalysisRunStatus, Claim } from '@/api/types';
import { primaryButton, surface } from '@/components/ui';
import { formatDate, formatPaise, titleCase } from '@/format';
import { cn } from '@/lib/utils';

const ANALYSED: AnalysisRunStatus[] = ['COMPLETED', 'COMPLETED_WITH_ABSTENTION'];
const FINISHED: AnalysisRunStatus[] = [
  'COMPLETED',
  'COMPLETED_WITH_ABSTENTION',
  'FAILED_INFRASTRUCTURE',
  'FAILED_VALIDATION',
];

export function Claims() {
  const [claims, setClaims] = useState<Claim[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    listClaims()
      .then(async ({ claims: rows }) => {
        if (cancelled) return;
        setClaims(rows);

        const missing = rows.filter((claim) => !claim.latestRunStatus);
        if (missing.length === 0) return;

        const updates = await Promise.all(
          missing.map(async (claim) => {
            try {
              const { runs } = await listRuns(claim.claimId);
              const status = pickRunStatus(runs);
              return status ? { claimId: claim.claimId, status } : null;
            } catch {
              return null;
            }
          }),
        );

        if (cancelled) return;
        setClaims((current) =>
          (current ?? rows).map((claim) => {
            const found = updates.find((update) => update?.claimId === claim.claimId);
            return found ? { ...claim, latestRunStatus: found.status } : claim;
          }),
        );
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Could not load claims.');
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="text-[17px] tracking-[-0.02em] text-neutral-400">Workspace</p>
          <h1 className="mt-1 text-4xl tracking-[-0.03em]">Your claims</h1>
        </div>
        <Link to="/claims/new" className={primaryButton}>
          New claim
        </Link>
      </div>

      {error && (
        <p role="alert" className="mt-6 rounded-xl bg-red-50/90 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      {!claims && !error && (
        <div className={cn(surface, 'mt-6 px-4 py-8 text-sm text-neutral-500')}>Loading…</div>
      )}

      {claims?.length === 0 && (
        <div className={cn(surface, 'mt-6 px-6 py-14 text-center')}>
          <h2 className="text-2xl tracking-[-0.03em] text-neutral-900">No claims yet</h2>
          <p className="mx-auto mt-2 max-w-md text-sm text-neutral-500">
            Add a PF Final Settlement claim to check it against published EPFO timelines. The
            claim details and analysis are saved, so you will not need to analyse the same claim
            again.
          </p>
          <Link to="/claims/new" className={cn(primaryButton, 'mt-6 inline-flex')}>
            Add your first claim
          </Link>
        </div>
      )}

      {claims && claims.length > 0 && (
        <ul className="mt-6 space-y-3">
          {claims.map((claim) => (
            <li key={claim.claimId}>
              <Link
                to={`/claims/${claim.claimId}`}
                className={cn(
                  surface,
                  'flex items-center justify-between px-4 py-3.5 transition hover:bg-white',
                )}
              >
                <div>
                  <p className="font-medium">{titleCase(claim.claimType)}</p>
                  <p className="text-sm text-neutral-500">
                    Filed {formatDate(claim.claimDateIso)} · {formatPaise(claim.amountPaise)}
                  </p>
                </div>
                <span className={badgeClass(claim.latestRunStatus)}>{runBadge(claim.latestRunStatus)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function pickRunStatus(runs: AnalysisRun[]): AnalysisRunStatus | undefined {
  return (
    runs.find((run) => ANALYSED.includes(run.status))?.status ??
    runs.find((run) => FINISHED.includes(run.status))?.status ??
    runs[0]?.status
  );
}

function runBadge(status: AnalysisRunStatus | null | undefined): string {
  if (status === 'COMPLETED' || status === 'COMPLETED_WITH_ABSTENTION') return 'Analysed';
  if (status === 'RUNNING') return 'Analysing';
  if (status === 'FAILED_INFRASTRUCTURE' || status === 'FAILED_VALIDATION') return 'Needs attention';
  return 'Not analysed';
}

function badgeClass(status: AnalysisRunStatus | null | undefined): string {
  const analysed = status === 'COMPLETED' || status === 'COMPLETED_WITH_ABSTENTION';
  return cn(
    'rounded-full px-3 py-1 text-xs font-medium',
    analysed ? 'bg-neutral-950 text-white' : 'bg-neutral-100/80 text-neutral-700',
  );
}
