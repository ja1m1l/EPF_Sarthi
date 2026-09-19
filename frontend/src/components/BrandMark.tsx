export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <path
        d="M32 6C21.5 18.2 15 28 15 39.2 15 49.8 22.4 58 32 58s17-8.2 17-18.8C49 28 42.5 18.2 32 6Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinejoin="round"
      />
      <path
        d="M21 36.8c3.8-6.4 7.8-9.6 11-9.6s7.2 3.2 11 9.6c-3.8 6.4-7.8 9.6-11 9.6s-7.2-3.2-11-9.6Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
      />
      <circle cx="32" cy="36.8" r="3.1" fill="currentColor" />
      <path d="M32 10.5v4.8" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}
