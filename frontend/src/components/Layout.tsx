import { LayoutGroup, motion } from 'motion/react';
import { useState, type ReactNode } from 'react';
import { HiArrowUpRight } from 'react-icons/hi2';
import { IoChevronDown } from 'react-icons/io5';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthContext';
import { loadProfile } from '@/auth/profile';
import { BrandMark } from '@/components/BrandMark';
import { ImageStrip } from '@/components/ImageStrip';
import { WordBackdrop } from '@/components/WordBackdrop';
import { config } from '@/config';
import { cn } from '@/lib/utils';

type NavKey = 'claims' | 'new';

export function Layout({ children }: { children: ReactNode }) {
  const { email, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [noticeOpen, setNoticeOpen] = useState(false);
  const isAuthPage = location.pathname === '/signin';
  const showAppNav = Boolean(email) && !isAuthPage;
  const active: NavKey = location.pathname === '/claims/new' ? 'new' : 'claims';

  const items: { key: NavKey; label: string; onClick: () => void }[] = [
    { key: 'claims', label: 'Claims', onClick: () => navigate('/claims') },
    { key: 'new', label: 'New claim', onClick: () => navigate('/claims/new') },
  ];

  const profile = email ? loadProfile(email) : null;
  const initials =
    [profile?.givenName, profile?.familyName]
      .map((part) => part?.trim().charAt(0))
      .join('')
      .toUpperCase() || email?.charAt(0).toUpperCase() || 'U';

  return (
    <div className="relative flex min-h-dvh flex-col overflow-x-hidden bg-white text-neutral-950">
      <WordBackdrop />
      <header className="relative z-20 w-full">
        <div className="relative flex w-full items-center justify-between px-5 py-4 sm:px-7">
          <Link to={email ? '/claims' : '/signin'} className="relative z-10 flex items-center gap-3">
            <BrandMark className="h-12 w-12 text-neutral-950 sm:h-14 sm:w-14" />
            <span className="text-3xl tracking-[-0.04em] sm:text-4xl">EPF Sarthi</span>
          </Link>

          {showAppNav && (
            <div className="absolute left-1/2 hidden -translate-x-1/2 md:block">
              <GlassNav items={items} active={active} layoutId="nav-liquid-glass-desktop" />
            </div>
          )}

          {showAppNav && (
            <div className="relative z-10 flex items-center gap-2">
              <button
                type="button"
                onClick={() => navigate('/profile')}
                aria-label="Open profile"
                className={cn(iconButtonClass, 'h-9 w-auto gap-2 px-2.5')}
              >
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-neutral-950 text-[11px] text-white">
                  {initials}
                </span>
                Profile
              </button>
              <button
                type="button"
                onClick={() => {
                  signOut();
                  navigate('/signin');
                }}
                className={cn(iconButtonClass, 'h-9 w-auto px-3')}
              >
                Sign out
              </button>
            </div>
          )}
        </div>

        {showAppNav && (
          <div className="flex justify-center px-5 pb-3 md:hidden">
            <GlassNav items={items} active={active} layoutId="nav-liquid-glass-mobile" />
          </div>
        )}
      </header>

      <main
        className={cn(
          'relative z-10 w-full flex-1',
          isAuthPage ? 'flex flex-col justify-center px-0' : 'mx-auto max-w-5xl px-6 py-6',
        )}
      >
        {children}
      </main>

      {isAuthPage && <ImageStrip />}
      <Disclaimer open={noticeOpen} onToggle={setNoticeOpen} />
    </div>
  );
}

const iconButtonClass = cn(
  'flex size-9 items-center justify-center rounded-full',
  'border border-neutral-200/80 bg-white/50 text-neutral-700',
  'shadow-[inset_0_1px_0_rgba(255,255,255,0.8)] backdrop-blur-xl',
  'hover:bg-white',
);

function GlassNav({
  items,
  active,
  layoutId,
}: {
  items: { key: NavKey; label: string; onClick: () => void }[];
  active: NavKey;
  layoutId: string;
}) {
  return (
    <LayoutGroup id={layoutId}>
      <nav
        className={cn(
          'flex items-center gap-1 rounded-full border border-white/70 bg-white/35 px-1.5 py-1.5',
          'text-[17px] tracking-[-0.02em] text-neutral-500',
          'shadow-[inset_0_1px_0_rgba(255,255,255,0.85),0_8px_30px_rgba(15,23,42,0.06)] backdrop-blur-2xl',
        )}
      >
        {items.map((item) => {
          const isActive = item.key === active;
          return (
            <button
              key={item.key}
              type="button"
              onClick={item.onClick}
              className={cn(
                'relative rounded-full px-3 py-1.5 transition',
                isActive ? 'text-neutral-950' : 'hover:text-neutral-800',
              )}
            >
              {isActive && (
                <motion.span
                  layoutId={layoutId}
                  className="absolute inset-0 rounded-full bg-white/70 ring-1 ring-white/80 backdrop-blur-xl"
                  transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                />
              )}
              <span className="relative z-10">{item.label}</span>
            </button>
          );
        })}
      </nav>
    </LayoutGroup>
  );
}

function Disclaimer({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  return (
    <footer id="notice" className="relative z-10 w-full">
      <div className="flex w-full items-stretch gap-2 px-5 py-4 sm:px-7">
        <div className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-neutral-200/70 bg-white/70 backdrop-blur-md">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => onToggle(!open)}
            className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left"
          >
            <span className="text-sm tracking-[-0.02em] text-neutral-600">
              Informational only — not legal advice
            </span>
            <IoChevronDown
              className={cn('size-4 shrink-0 text-neutral-400 transition-transform', open && 'rotate-180')}
            />
          </button>

          {open && (
            <div className="space-y-2 border-t border-neutral-200 px-4 py-3 text-sm leading-relaxed text-neutral-600">
              <p>
                EPF Sarthi is an independent tool and is{' '}
                <strong className="text-neutral-800">
                  not affiliated with, endorsed by, or operated by EPFO
                </strong>
                . It compares dates you enter against published EPFO timelines and never asserts
                that EPFO is at fault.
              </p>
              <p>
                <strong className="text-neutral-800">Nothing is submitted automatically.</strong> Any
                grievance draft is yours to review, edit, and submit yourself on the official EPFiGMS
                portal.
              </p>
              <p>
                Claim text and uploaded document text are processed by the Google Gemini API, a
                third-party model provider. UAN, bank account numbers, and credentials are redacted
                in code before any outbound call. See{' '}
                <Link to="/privacy" className="underline">
                  Privacy and help
                </Link>
                .
              </p>
            </div>
          )}
        </div>
        <a
          href={config.epfigmsUrl}
          target="_blank"
          rel="noreferrer noopener"
          aria-label="Open the official EPFiGMS portal"
          className={cn(
            iconButtonClass,
            'h-auto min-h-11 w-11 shrink-0 self-stretch rounded-2xl',
          )}
        >
          <HiArrowUpRight className="size-4" />
        </a>
      </div>
      <div className="px-5 pb-4 sm:px-7">
        <Link to="/privacy" className="text-sm text-neutral-500 underline">
          Privacy and help
        </Link>
      </div>
    </footer>
  );
}
