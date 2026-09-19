import { useState } from 'react';

import type { GrievanceDraft } from '@/api/types';
import { primaryButton, surface } from '@/components/ui';
import { config } from '@/config';
import { cn } from '@/lib/utils';

export function GrievancePanel({ draft }: { draft: GrievanceDraft | undefined }) {
  const [reviewed, setReviewed] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!draft?.draftText) return null;

  async function handleCopy() {
    await navigator.clipboard.writeText(draft!.draftText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  }

  return (
    <section className={cn(surface, 'p-5')}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Draft grievance
      </h2>
      <p className="mt-2 text-sm text-neutral-600">
        A draft for you to review and submit yourself. EPF Sentinel does not send it anywhere.
      </p>

      <textarea
        readOnly
        value={draft.draftText}
        rows={16}
        className="mt-4 w-full resize-y rounded-md bg-white/80 p-3 font-mono text-xs leading-relaxed text-neutral-800 shadow-[inset_0_0_0_1px_#e5e5e5]"
      />

      <label className="mt-4 flex items-start gap-2 text-sm text-neutral-700">
        <input
          type="checkbox"
          checked={reviewed}
          onChange={(e) => setReviewed(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          I have reviewed this draft and confirmed the details are accurate.
        </span>
      </label>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        {/* Copying stays disabled until the draft is acknowledged as reviewed. */}
        <button
          type="button"
          disabled={!reviewed}
          onClick={handleCopy}
          className={cn(primaryButton, 'disabled:cursor-not-allowed disabled:opacity-40')}
        >
          {copied ? 'Copied' : 'Copy draft'}
        </button>

        <a
          href={config.epfigmsUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="text-sm font-medium text-sky-700 underline underline-offset-2 hover:text-sky-900"
        >
          Open the official EPFiGMS portal
        </a>
      </div>
    </section>
  );
}
