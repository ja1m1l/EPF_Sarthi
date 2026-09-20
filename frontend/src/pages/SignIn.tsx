import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { useAuth } from '@/auth/AuthContext';
import { insetField } from '@/components/ui';
import { cn } from '@/lib/utils';

type Mode = 'signin' | 'signup' | 'confirm' | 'forgot' | 'reset';

export function SignIn() {
  const { signIn, signUp, confirmSignUp, resendCode, forgotPassword, confirmForgotPassword } =
    useAuth();
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
    setMode((current) =>
      current === 'confirm' || current === 'forgot' || current === 'reset' ? current : next,
    );
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
      } else if (mode === 'forgot') {
        await forgotPassword(email);
        setMode('reset');
        setNotice('If an account exists for that email, we sent a reset code.');
      } else if (mode === 'reset') {
        await confirmForgotPassword(email, code, password);
        await signIn(email, password);
        navigate('/claims');
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
    mode === 'signin'
      ? 'Sign in'
      : mode === 'signup'
        ? 'Create an account'
        : mode === 'confirm'
          ? 'Confirm your email'
          : mode === 'forgot'
            ? 'Reset password'
            : 'Enter the reset code';

  return (
    <div className="flex w-full flex-1 flex-col items-center px-5 sm:px-7">
      <section className="relative mx-auto flex w-full max-w-3xl flex-col items-center pt-8 pb-8 text-center sm:pt-10">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute top-[42%] left-1/2 h-48 w-[28rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle_at_center,rgba(250,236,180,0.55),rgba(255,214,170,0.18)_48%,transparent_72%)] blur-3xl"
        />

        <h1 className="relative text-[2.75rem] leading-[1.08] tracking-[-0.04em] text-neutral-950 sm:text-6xl md:text-7xl">
          Know when the
          <br />
          timeline has passed
        </h1>
      </section>

      <form
        id="auth"
        onSubmit={handleSubmit}
        className="relative z-10 mx-auto mb-6 w-full max-w-md space-y-4 rounded-[1.75rem] border border-white/80 bg-white/65 p-6 shadow-[0_18px_50px_-28px_rgba(15,23,42,0.35)] backdrop-blur-2xl"
      >
        <div>
          <h2 className="text-2xl tracking-[-0.03em]">{title}</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-neutral-500">
            Check your PF final settlement against published EPFO timelines.
          </p>
        </div>

        <Field label="Email">
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={mode === 'confirm' || mode === 'reset'}
            className={cn(insetField, 'rounded-full px-4')}
          />
        </Field>

        {mode !== 'forgot' && (
          <Field
            label={mode === 'reset' ? 'New password' : 'Password'}
            hint={mode === 'signup' || mode === 'reset' ? 'At least 8 characters, with upper, lower, and a number.' : undefined}
          >
            <input
              type="password"
              required
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={cn(insetField, 'rounded-full px-4')}
            />
          </Field>
        )}

        {(mode === 'confirm' || mode === 'reset') && (
          <Field label={mode === 'reset' ? 'Reset code' : 'Confirmation code'}>
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
                : mode === 'forgot'
                  ? 'Send reset code'
                  : mode === 'reset'
                    ? 'Set new password'
                    : 'Confirm'}
        </button>

        <div className="space-y-2 text-center text-sm text-neutral-500">
          {mode === 'signin' && (
            <>
              <button type="button" onClick={() => setMode('forgot')} className="block w-full underline">
                Forgot password?
              </button>
              <button type="button" onClick={() => setMode('signup')} className="underline">
                Need an account? Sign up
              </button>
            </>
          )}
          {(mode === 'signup' || mode === 'forgot' || mode === 'reset') && (
            <button type="button" onClick={() => setMode('signin')} className="underline">
              Back to sign in
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
