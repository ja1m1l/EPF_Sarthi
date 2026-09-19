/** Money is integer paise on the wire; it is only ever divided for display. */
export function formatPaise(paise: number): string {
  const rupees = Math.trunc(paise / 100);
  const remainder = Math.abs(paise % 100);
  const whole = new Intl.NumberFormat('en-IN').format(rupees);
  return remainder === 0 ? `₹${whole}` : `₹${whole}.${String(remainder).padStart(2, '0')}`;
}

/** Render an ISO date (or full timestamp) as a readable IST-style date. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  }).format(date);
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}
