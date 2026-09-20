import { useEffect, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { getClaim, getRun, listRuns, startAnalysis } from '@/api/client';
import type { AnalysisRun, Claim } from '@/api/types';
import { EvidenceTable } from '@/components/EvidenceTable';
import { GrievancePanel } from '@/components/GrievancePanel';
import { RuleCard } from '@/components/RuleCard';
import { SlaCard } from '@/components/SlaCard';
import { primaryButton, surface } from '@/components/ui';
import { formatDate, formatPaise, titleCase } from '@/format';
import { cn } from '@/lib/utils';

const POLL_MS = 2000;
const POLL_TIMEOUT_MS = 120_000;

const KEPT_STATUSES: AnalysisRun['status'][] = ['COMPLETED', 'COMPLETED_WITH_ABSTENTION'];
const FINISHED_STATUSES: AnalysisRun['status'][] = [
  'COMPLETED',
  'COMPLETED_WITH_ABSTENTION',
  'FAILED_INFRASTRUCTURE',
  'FAILED_VALIDATION',
];

/** Prefer a finished saved result over a newer still-running attempt. */
function pickStoredRun(runs: AnalysisRun[]): AnalysisRun | undefined {
  return (
    runs.find((run) => KEPT_STATUSES.includes(run.status)) ??
    runs.find((run) => FINISHED_STATUSES.includes(run.status)) ??
    runs[0]
  );
}

export function ClaimDetail() {
  const { claimId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get('runId');

  const [claim, setClaim] = useState<Claim | null>(null);
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [lookingForRun, setLookingForRun] = useState(!runId);

  useEffect(() => {
    getClaim(claimId)
      .then(setClaim)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load claim.'));
  }, [claimId]);

  // Opening a claim from the list has no runId. Load the newest stored run
  // so a finished analysis is shown instead of "not analysed yet".
  useEffect(() => {
    if (runId || !claimId) {
      setLookingForRun(false);
      return;
    }

    let cancelled = false;
    setLookingForRun(true);

    listRuns(claimId)
      .then(({ runs }) => {
        if (cancelled) return;
        const stored = pickStoredRun(runs);
        if (stored) {
          setSearchParams({ runId: stored.runId }, { replace: true });
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Could not load the analysis.');
        }
      })
      .finally(() => {
        if (!cancelled) setLookingForRun(false);
      });

    return () => {
      cancelled = true;
    };
  }, [claimId, runId, setSearchParams]);

  // Poll until the run reaches a terminal status. RUNNING is the only
  // non-terminal one; the four terminal statuses each render differently.
  const pollDeadline = useRef<number>(0);
  useEffect(() => {
    if (!runId) return;

    let cancelled = false;
    pollDeadline.current = Date.now() + POLL_TIMEOUT_MS;
    setTimedOut(false);

    async function poll() {
      if (cancelled) return;

      try {
        const next = await getRun(claimId, runId!);
        if (cancelled) return;
        setRun(next);

        if (next.status !== 'RUNNING') return;
      } catch (err) {
        // A run row may briefly 404 right after the execution starts.
        if (cancelled) return;
        if ((err as { status?: number }).status !== 404) {
          setError(err instanceof Error ? err.message : 'Could not load the analysis.');
          return;
        }
      }

      if (Date.now() > pollDeadline.current) {
        setTimedOut(true);
        return;
      }
      setTimeout(poll, POLL_MS);
    }

    poll();
    return () => {
      cancelled = true;
    };
  }, [claimId, runId]);

  async function handleAnalyze() {
    setRestarting(true);
    setError(null);
    setRun(null);
    try {
      const started = await startAnalysis(claimId);
      setSearchParams({ runId: started.runId });
    } catch (err) {
      const code = (err as { code?: string }).code;
      setError(
        code === 'ANALYSIS_CAP_EXCEEDED'
          ? 'You have reached today’s analysis limit (20). Open a saved result instead, or try again tomorrow.'
          : err instanceof Error
            ? err.message
            : 'Could not start the analysis.',
      );
    } finally {
      setRestarting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <Link to="/claims" className="text-sm text-neutral-500 underline underline-offset-2">
          ← All claims
        </Link>
        <h1 className="mt-2 text-4xl tracking-[-0.03em]">Claim result</h1>
      </div>

      {claim && <ClaimSummary claim={claim} />}

      {error && (
        <p role="alert" className="rounded-xl bg-red-50/90 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      {!runId && claim && !lookingForRun && (
        <div className={cn(surface, 'p-5')}>
          <p className="text-sm text-neutral-600">This claim has not been analysed yet.</p>
          <button
            type="button"
            onClick={handleAnalyze}
            disabled={restarting}
            className={cn(primaryButton, 'mt-3')}
          >
            {restarting ? 'Starting…' : 'Run analysis'}
          </button>
        </div>
      )}

      {lookingForRun && <PendingState label="Loading saved analysis…" />}

      {runId && !run && !error && !timedOut && <PendingState label="Loading analysis…" />}

      {run?.status === 'RUNNING' && !timedOut && (
        <PendingState label="Analysing your claim against published EPFO rules…" />
      )}

      {timedOut && (
        <div className="rounded-xl border border-amber-300/70 bg-amber-50/80 p-5 backdrop-blur-sm">
          <h2 className="font-medium text-amber-900">This is taking longer than expected</h2>
          <p className="mt-1 text-sm text-amber-800">
            The analysis has not finished yet. It may still complete — reload in a moment.
          </p>
        </div>
      )}

      {/*
        Infrastructure failure is rendered as an error the user can retry.
        It is deliberately a different screen from an abstention, which is a
        valid, finished result.
      */}
      {run?.status === 'FAILED_INFRASTRUCTURE' && (
        <FailureState
          title="The analysis could not be completed"
          body="Something in our pipeline failed before we could reach a result. This is a problem on our side, not a finding about your claim. Nothing below should be read as a verdict."
          detail={run.failureDetail?.errorType}
          onRetry={handleAnalyze}
          retrying={restarting}
        />
      )}

      {run?.status === 'FAILED_VALIDATION' && (
        <FailureState
          title="We could not read this claim"
          body="The claim details did not pass validation, so no analysis was run. Check the dates and amount on the claim and try again."
          detail={run.failureDetail?.errorMessage}
        />
      )}

      {(run?.status === 'COMPLETED' || run?.status === 'COMPLETED_WITH_ABSTENTION') && (
        <>
          <SlaCard sla={run.slaAgentOutputJson} />
          <RuleCard rules={run.rulesAgentOutputJson} />
          <EvidenceTable report={run.evidenceAgentOutputJson} />
          <GrievancePanel draft={run.grievanceAgentOutputJson} />
        </>
      )}

      {run && run.status !== 'RUNNING' && (
        <p className="text-xs text-neutral-400">
          Run {run.runId} · correlation {run.correlationId}
        </p>
      )}
    </div>
  );
}

function ClaimSummary({ claim }: { claim: Claim }) {
  return (
    <section className={cn(surface, 'p-5')}>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
        <Item label="Claim type" value={titleCase(claim.claimType)} />
        <Item label="Claim date" value={formatDate(claim.claimDateIso)} />
        <Item label="Amount" value={formatPaise(claim.amountPaise)} />
        <Item label="Status" value={titleCase(claim.status)} />
        {claim.deficiencyRaisedDateIso && (
          <Item label="Deficiency raised" value={formatDate(claim.deficiencyRaisedDateIso)} />
        )}
        {claim.notes && <Item label="Notes" value={claim.notes} />}
      </dl>
    </section>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className="mt-0.5 font-medium text-neutral-900">{value}</dd>
    </div>
  );
}

function PendingState({ label }: { label: string }) {
  return (
    <div className={cn(surface, 'flex items-center gap-3 p-5')}>
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-800" />
      <p className="text-sm text-neutral-600">{label}</p>
    </div>
  );
}

function FailureState({
  title,
  body,
  detail,
  onRetry,
  retrying,
}: {
  title: string;
  body: string;
  detail?: string;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  return (
    <div className="rounded-xl border-2 border-red-400/80 bg-red-50/85 p-5 backdrop-blur-sm">
      <h2 className="font-semibold text-red-900">{title}</h2>
      <p className="mt-1 text-sm text-red-800">{body}</p>
      {detail && (
        <p className="mt-2 font-mono text-xs text-red-700">{detail}</p>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="mt-3 rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-800 disabled:opacity-50"
        >
          {retrying ? 'Retrying…' : 'Try again'}
        </button>
      )}
    </div>
  );
}
