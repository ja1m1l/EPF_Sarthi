import type { CitedSource, RulesDecision } from '@/api/types';
import { surface } from '@/components/ui';
import { formatDate } from '@/format';
import { cn } from '@/lib/utils';

export function RuleCard({ rules }: { rules: RulesDecision | undefined }) {
  if (!rules) return null;

  // An abstention is a normal outcome, not an error — and never a reason to
  // show a default number. We say plainly that nothing matched.
  if (!rules.applicable) {
    return (
      <section className={cn(surface, 'p-5')}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
          Applicable rule
        </h2>
        <p className="mt-3 font-medium text-neutral-800">
          No applicable published rule was matched.
        </p>
        <p className="mt-2 text-sm text-neutral-600">
          We could not find a published EPFO timeline in our rules corpus that covers this
          claim, so no deadline has been calculated. We do not substitute an assumed number.
        </p>
        {rules.abstainReason && (
          <p className="mt-3 text-xs text-neutral-500">
            Reason code: <code className="font-mono">{rules.abstainReason}</code>
          </p>
        )}
      </section>
    );
  }

  const sources = dedupeSources(rules);

  return (
    <section className={cn(surface, 'p-5')}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Applicable rule
      </h2>

      <p className="mt-3 text-lg font-medium text-neutral-900">
        {rules.timelineDays} {rules.timelineBasis === 'WORKING' ? 'working' : 'calendar'} days
      </p>

      {rules.charterTargetDays !== null && rules.charterTargetDays !== undefined && (
        <p className="mt-1 text-sm text-neutral-600">
          EPFO's Citizens' Charter states a separate target of {rules.charterTargetDays} days.
          The statutory figure above is the one used for the deadline.
        </p>
      )}

      {rules.quotedSpan && (
        <blockquote className="mt-4 border-l-4 border-neutral-300 bg-white/70 py-2 pl-4 pr-3 text-sm italic leading-relaxed text-neutral-800">
          “{rules.quotedSpan}”
        </blockquote>
      )}

      {sources.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Source
          </h3>
          <ul className="mt-2 space-y-2">
            {sources.map((source) => (
              <li key={source.sourceUrl} className="text-sm">
                <a
                  href={source.sourceUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-medium text-sky-700 underline underline-offset-2 hover:text-sky-900"
                >
                  {source.sourceTitle || source.sourceUrl}
                </a>
                {source.retrievedOn && (
                  <span className="ml-2 text-xs text-neutral-500">
                    retrieved {formatDate(source.retrievedOn)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-4 text-xs text-neutral-500">
        Confidence: {rules.confidence.toLowerCase()}. The quoted text above was verified in code
        to appear verbatim in the cited source before this timeline was accepted.
      </p>
    </section>
  );
}

/**
 * Prefer citedSources, which the backend builds from the retrieved chunks and
 * therefore carries a title and retrieval date. Runs persisted before that
 * field existed only have bare URLs, so fall back to those.
 */
function dedupeSources(rules: RulesDecision): CitedSource[] {
  if (rules.citedSources?.length) {
    const seen = new Map<string, CitedSource>();
    for (const source of rules.citedSources) {
      if (!seen.has(source.sourceUrl)) seen.set(source.sourceUrl, source);
    }
    return [...seen.values()];
  }

  return [...new Set(rules.citedSourceUrls)].map((sourceUrl) => ({
    chunkId: '',
    sourceUrl,
    sourceTitle: sourceUrl,
    retrievedOn: '',
    authority: '',
  }));
}
