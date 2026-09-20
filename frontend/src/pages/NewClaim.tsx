import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { createClaim, getDocument, startAnalysis, uploadDocument } from '@/api/client';
import type { ClaimStatus } from '@/api/types';
import { DocumentDropzone } from '@/components/DocumentDropzone';
import { insetField, primaryButton, surface } from '@/components/ui';
import { cn } from '@/lib/utils';

const STATUSES: ClaimStatus[] = [
  'SUBMITTED',
  'PENDING',
  'UNDER_PROCESS',
  'REJECTED',
  'SETTLED',
];

const DOCUMENT_SLOTS = [
  {
    kind: 'KYC',
    label: 'KYC / identity proof',
    hint: 'Aadhaar, PAN, or the KYC page from the member portal.',
  },
  {
    kind: 'BANK',
    label: 'Bank details',
    hint: 'Passbook, cancelled cheque, or the bank details screen.',
  },
  {
    kind: 'DATE_OF_EXIT',
    label: 'Date of exit',
    hint: 'Relieving letter or the exit date shown on the claim.',
  },
  {
    kind: 'DEFICIENCY',
    label: 'Deficiency communication',
    hint: 'The letter or portal notice, if EPFO raised a deficiency.',
  },
  {
    kind: 'CLAIM_AMOUNT',
    label: 'Claim form / amount',
    hint: 'Form 19 or the settlement amount screen.',
  },
] as const;

const EXTRACTION_POLL_MS = 2000;
const EXTRACTION_TIMEOUT_MS = 90_000;

/**
 * Wait until every uploaded document has left PROCESSING.
 *
 * The analysis only picks up documents in EXTRACTED state, so starting it
 * while extraction is still running would produce an evidence report of all
 * NOT_FOUND that looks identical to having uploaded nothing.
 */
async function waitForExtraction(claimId: string, documentIds: string[]): Promise<void> {
  const deadline = Date.now() + EXTRACTION_TIMEOUT_MS;
  const pending = new Set(documentIds);

  while (pending.size > 0 && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, EXTRACTION_POLL_MS));

    for (const documentId of [...pending]) {
      try {
        const doc = await getDocument(claimId, documentId);
        if (doc.status !== 'PROCESSING') {
          pending.delete(documentId);
        }
      } catch {
        // The document row may not be readable yet; retry on the next tick.
      }
    }
  }
}

export function NewClaim() {
  const navigate = useNavigate();

  const [claimDate, setClaimDate] = useState('');
  const [amountRupees, setAmountRupees] = useState('');
  const [status, setStatus] = useState<ClaimStatus>('PENDING');
  const [deficiencyDate, setDeficiencyDate] = useState('');
  const [notes, setNotes] = useState('');
  const [documents, setDocuments] = useState<Record<string, File | null>>({});

  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);

  const busy = step !== null;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setFieldError(null);

    try {
      setStep('Creating claim…');
      const claim = await createClaim({
        claimType: 'FINAL_SETTLEMENT',
        claimDate,
        amountRupees,
        status,
        ...(deficiencyDate ? { deficiencyRaisedDate: deficiencyDate } : {}),
        ...(notes.trim() ? { notes: notes.trim() } : {}),
      });

      const labeled = DOCUMENT_SLOTS.flatMap((slot) => {
        const file = documents[slot.kind];
        return file ? [{ file, kind: slot.kind }] : [];
      });

      if (labeled.length > 0) {
        setStep(`Uploading ${labeled.length} document${labeled.length > 1 ? 's' : ''}…`);
        const documentIds: string[] = [];
        for (const { file, kind } of labeled) {
          documentIds.push(await uploadDocument(claim.claimId, file, kind));
        }

        setStep('Reading uploaded documents…');
        await waitForExtraction(claim.claimId, documentIds);
      }

      setStep('Starting analysis…');
      const run = await startAnalysis(claim.claimId);

      navigate(`/claims/${claim.claimId}?runId=${run.runId}`);
    } catch (err) {
      const code = (err as { code?: string }).code;
      const message =
        code === 'ANALYSIS_CAP_EXCEEDED'
          ? 'You have reached today’s analysis limit (20). The saved claims are still here — try again tomorrow.'
          : err instanceof Error
            ? err.message
            : 'Something went wrong.';
      const field = (err as { field?: string }).field;
      if (field) setFieldError(field);
      setError(message);
      setStep(null);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <p className="text-[17px] tracking-[-0.02em] text-neutral-400">New analysis</p>
      <h1 className="mt-1 text-4xl tracking-[-0.03em]">New claim</h1>
      <p className="mt-2 text-sm text-neutral-600">
        Enter the claim exactly as it appears on the EPFO member portal. Nothing here is sent
        to EPFO.
      </p>

      <form onSubmit={handleSubmit} className={cn(surface, 'mt-6 space-y-5 p-5')}>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium tracking-wide text-neutral-500">
            Claim type
          </span>
          <select
            value="FINAL_SETTLEMENT"
            disabled
            className={cn(insetField, 'disabled:bg-neutral-100')}
          >
            <option value="FINAL_SETTLEMENT">PF Final Settlement (Form 19)</option>
            <option disabled>Pension (EPS) — coming soon</option>
            <option disabled>Transfer claim — coming soon</option>
            <option disabled>Advance / partial withdrawal — coming soon</option>
          </select>
        </label>

        <div className="grid gap-5 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium tracking-wide text-neutral-500">
              Claim date
            </span>
            <input
              type="date"
              required
              value={claimDate}
              onChange={(e) => setClaimDate(e.target.value)}
              className={inputClass(fieldError === 'claimDate')}
            />
          </label>

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium tracking-wide text-neutral-500">
              Amount (₹)
            </span>
            <input
              type="number"
              required
              min="1"
              step="0.01"
              placeholder="480000"
              value={amountRupees}
              onChange={(e) => setAmountRupees(e.target.value)}
              className={inputClass(fieldError === 'amountRupees')}
            />
          </label>

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium tracking-wide text-neutral-500">
              Current status
            </span>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as ClaimStatus)}
              className={inputClass(fieldError === 'status')}
            >
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium tracking-wide text-neutral-500">
              Deficiency raised on <span className="font-normal">(optional)</span>
            </span>
            <input
              type="date"
              value={deficiencyDate}
              onChange={(e) => setDeficiencyDate(e.target.value)}
              className={inputClass(fieldError === 'deficiencyRaisedDate')}
            />
          </label>
        </div>

        <label className="block">
          <span className="mb-1.5 block text-xs font-medium tracking-wide text-neutral-500">
            Notes <span className="font-normal">(optional)</span>
          </span>
          <textarea
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Anything else about this claim."
            className={insetField}
          />
          <span className="mt-1 block text-xs text-neutral-500">
            Do not include your UAN, bank account number, or passwords.
          </span>
        </label>

        <div>
          <span className="block text-sm tracking-[-0.02em] text-neutral-700">
            Documents <span className="text-neutral-400">(optional)</span>
          </span>
          <p className="mt-1 text-xs text-neutral-500">
            Add each file next to its name so the analysis knows what it is looking at.
          </p>
          <div className="mt-3 divide-y divide-neutral-200/80">
            {DOCUMENT_SLOTS.map((slot) => (
              <div key={slot.kind} className="py-3 first:pt-1 last:pb-0">
                <DocumentDropzone
                  label={slot.label}
                  hint={slot.hint}
                  file={documents[slot.kind] ?? null}
                  onFile={(file) => setDocuments((current) => ({ ...current, [slot.kind]: file }))}
                  disabled={busy}
                />
              </div>
            ))}
          </div>
        </div>

        {error && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        <button type="submit" disabled={busy} className={cn(primaryButton, 'w-full')}>
          {step ?? 'Check this claim'}
        </button>
      </form>
    </div>
  );
}

function inputClass(hasError: boolean): string {
  return cn(insetField, hasError && 'bg-red-50 shadow-[inset_0_0_0_1px_#ef4444]');
}
