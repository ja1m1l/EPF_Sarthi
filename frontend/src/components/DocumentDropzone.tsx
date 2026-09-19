import { useRef, useState } from 'react';

import { cn } from '@/lib/utils';

const ACCEPTED = ['image/png', 'image/jpeg', 'application/pdf'];
const MAX_BYTES = 10 * 1024 * 1024;

const sideButton = cn(
  'shrink-0 rounded-full px-3.5 py-1.5 text-xs tracking-[-0.01em]',
  'shadow-[inset_0_0_0_1px_#e5e5e5]',
  'transition hover:bg-white hover:shadow-[inset_0_0_0_1px_#0f172a]',
  'disabled:cursor-not-allowed disabled:opacity-50',
);

export function DocumentDropzone({
  file,
  onFile,
  label,
  hint,
  disabled,
}: {
  file: File | null;
  onFile: (file: File | null) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [rejected, setRejected] = useState<string | null>(null);

  function accept(incoming: FileList | null) {
    if (!incoming?.[0]) return;
    const next = incoming[0];

    if (!ACCEPTED.includes(next.type)) {
      setRejected(`${next.name} — only PNG, JPEG, and PDF are accepted.`);
      return;
    }
    if (next.size > MAX_BYTES) {
      setRejected(`${next.name} — larger than the 10 MB limit.`);
      return;
    }

    setRejected(null);
    onFile(next);
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <span className="block text-sm tracking-[-0.02em] text-neutral-800">{label}</span>
          <p className="mt-0.5 truncate text-xs text-neutral-500">
            {file ? file.name : hint}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {file ? (
            <>
              <button
                type="button"
                disabled={disabled}
                onClick={() => inputRef.current?.click()}
                className={sideButton}
              >
                Replace
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() => {
                  setRejected(null);
                  onFile(null);
                }}
                className={cn(sideButton, 'text-neutral-500')}
              >
                Remove
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={disabled}
              onClick={() => inputRef.current?.click()}
              aria-label={`Add ${label}`}
              className={sideButton}
            >
              Add
            </button>
          )}
        </div>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED.join(',')}
        className="hidden"
        onChange={(e) => {
          accept(e.target.files);
          e.target.value = '';
        }}
      />

      {rejected && <p className="mt-2 text-xs text-red-700">{rejected}</p>}
    </div>
  );
}
