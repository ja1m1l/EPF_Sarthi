import { cn } from '@/lib/utils';

export const surface = cn(
  'rounded-xl border border-neutral-200/70',
  'bg-linear-to-b from-white/90 to-white/70',
  'shadow-[inset_0_0_0_1px_#ffffff80,0_12px_40px_-20px_rgba(15,23,42,0.28)]',
  'backdrop-blur-md',
);

export const insetField = cn(
  'w-full rounded-md px-3 py-2 text-sm text-neutral-800',
  'bg-white/50',
  'shadow-[inset_0_0_0_1px_rgba(229,229,229,0.7),inset_0_2px_0_0_rgba(255,255,255,0.7)]',
  'outline-none placeholder:text-neutral-400 backdrop-blur-sm',
  'focus:shadow-[inset_0_0_0_1px_#0f172a,inset_0_2px_0_0_#ffffff]',
  'disabled:opacity-60',
);

export const primaryButton = cn(
  'inline-flex items-center justify-center rounded-full px-5 py-2.5',
  'bg-neutral-950 text-sm text-white',
  'transition hover:bg-neutral-800',
  'disabled:cursor-not-allowed disabled:opacity-50',
);
