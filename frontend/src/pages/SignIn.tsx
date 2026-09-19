import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { useAuth } from '@/auth/AuthContext';
import { insetField } from '@/components/ui';
import { cn } from '@/lib/utils';

type Mode = 'signin' | 'signup' | 'confirm';

export function SignIn() {
  const { signIn, signUp, confirmSignUp, resendCode } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [mode, setMode] = useState<Mode>(searchParams.get('mode') === 'signup' ? 'signup' : 'signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const next = searchParams.get('mode') === 'signup' ? 'signup' : 'signin';
    setMode((current) => (current === 'confirm' ? current : next));
  }, [searchParams]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);

    try {
      if (mode === 'signin') {
        await signIn(email, password);
        navigate('/claims');
      } else if (mode === 'signup') {
        const { confirmationRequired } = await signUp(email, password);
        if (confirmationRequired) {
          setMode('confirm');
          setNotice(`We emailed a confirmation code to ${email}.`);
        } else {
          await signIn(email, password);
          navigate('/claims');
        }
      } else {
        await confirmSignUp(email, code);
        await signIn(email, password);
        navigate('/claims');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  }

  const title =
    mode === 'signin' ? 'Sign in' : mode === 'signup' ? 'Create an account' : 'Confirm your email';

  return (
    <div className="px-6">
      <section className="relative mx-auto flex max-w-4xl flex-col items-center justify-center pt-10 pb-8 text-center">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 left-1/2 h-64 w-136 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle_at_center,rgba(214,232,122,0.55),rgba(255,196,140,0.28)_42%,transparent_70%)] blur-2xl"
        />

        <h1 className="relative text-5xl leading-[1.05] tracking-[-0.03em] text-neutral-950 sm:text-6xl md:text-7xl">
          Know when the
          <br />
          timeline has passed
        </h1>
        <p className="relative mt-8 text-sm text-neutral-500">Welcome to EPF Sentinel</p>
      </section>

      <form
        id="auth"
        onSubmit={handleSubmit}
        className="mx-auto mt-2 w-full max-w-md space-y-3 rounded-3xl border border-neutral-200 bg-white p-5"
      >
        <h2 className="text-2xl tracking-[-0.02em]">{title}</h2>
        <p className="text-sm text-neutral-500">
          Track whether your EPF final settlement claim has passed its published timeline.
        </p>

        <Field label="Email">
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={mode === 'confirm'}
            className={cn(insetField, 'rounded-full px-4')}
          />
        </Field>

        <Field label="Password" hint={mode === 'signup' ? 'At least 8 characters.' : undefined}>
          <input
            type="password"
            required
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={cn(insetField, 'rounded-full px-4')}
          />
        </Field>

        {mode === 'confirm' && (
          <Field label="Confirmation code">
            <input
              type="text"
              required
              inputMode="numeric"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className={cn(insetField, 'rounded-full px-4')}
            />
          </Field>
        )}

        {notice && <p className="rounded-full bg-sky-50 px-4 py-2 text-sm text-sky-800">{notice}</p>}
        {error && (
          <p role="alert" className="rounded-full bg-red-50 px-4 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="inline-flex w-full items-center justify-center rounded-full bg-neutral-950 px-6 py-2.5 text-sm text-white disabled:opacity-50"
        >
          {busy
            ? 'Working…'
            : mode === 'signin'
              ? 'Sign in'
              : mode === 'signup'
                ? 'Sign up'
                : 'Confirm'}
        </button>

        <div className="text-center text-sm text-neutral-500">
          {mode === 'signin' && (
            <button type="button" onClick={() => setMode('signup')} className="underline">
              Need an account? Sign up
            </button>
          )}
          {mode === 'signup' && (
            <button type="button" onClick={() => setMode('signin')} className="underline">
              Already have an account? Sign in
            </button>
          )}
          {mode === 'confirm' && (
            <button
              type="button"
              onClick={async () => {
                try {
                  await resendCode(email);
                  setNotice('A new code is on its way.');
                } catch (err) {
                  setError(err instanceof Error ? err.message : 'Could not resend the code.');
                }
              }}
              className="underline"
            >
              Resend confirmation code
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-left">
      <span className="mb-1.5 block text-xs font-medium tracking-wide text-neutral-500">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-neutral-500">{hint}</span>}
    </label>
  );
}
