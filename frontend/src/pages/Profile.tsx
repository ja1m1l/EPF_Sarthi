import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { listClaims, deleteAccount } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import * as cognito from '@/auth/cognito';
import { clearProfile, emptyProfile, loadProfile, saveProfile, type ProfileDetails } from '@/auth/profile';
import { insetField, primaryButton, surface } from '@/components/ui';
import { cn } from '@/lib/utils';

export function Profile() {
  const { email, signOut } = useAuth();
  const navigate = useNavigate();

  const [account, setAccount] = useState<{
    email: string;
    sub: string;
    verified: boolean;
  } | null>(null);
  const [details, setDetails] = useState<ProfileDetails>(emptyProfile());
  const [claimCount, setClaimCount] = useState<number | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [passwordNotice, setPasswordNotice] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState('');
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;

    Promise.all([cognito.getIdTokenPayload(), cognito.getUserAttributes()]).then(
      ([payload, attrs]) => {
        if (cancelled) return;
        const address = payload?.email ?? email ?? '';
        const verifiedRaw = String(attrs.email_verified ?? payload?.email_verified ?? '');
        setAccount({
          email: address,
          sub: payload?.sub ?? '',
          verified: verifiedRaw.toLowerCase() === 'true',
        });
        const stored = loadProfile(address);
        setDetails({
          givenName: stored.givenName || attrs.given_name || attrs.name || '',
          familyName: stored.familyName || attrs.family_name || '',
          phone: stored.phone || attrs.phone_number || '',
          city: stored.city || attrs.address || '',
          preferredLanguage: stored.preferredLanguage,
        });
      },
    );

    listClaims()
      .then(({ claims }) => {
        if (!cancelled) setClaimCount(claims.length);
      })
      .catch(() => {
        if (!cancelled) setClaimCount(null);
      });

    return () => {
      cancelled = true;
    };
  }, [email]);

  function handleSave(event: FormEvent) {
    event.preventDefault();
    if (!account?.email) return;
    setSaveError(null);
    try {
      saveProfile(account.email, details);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Could not save your details.');
    }
  }

  async function handlePassword(event: FormEvent) {
    event.preventDefault();
    setPasswordError(null);
    setPasswordNotice(null);
    setPasswordBusy(true);
    try {
      await cognito.changePassword(currentPassword, newPassword);
      setCurrentPassword('');
      setNewPassword('');
      setPasswordNotice('Password updated.');
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : 'Could not update the password.');
    } finally {
      setPasswordBusy(false);
    }
  }

  const initials = [details.givenName, details.familyName]
    .map((part) => part.trim().charAt(0))
    .join('')
    .toUpperCase() || (account?.email.charAt(0).toUpperCase() ?? 'U');

  return (
    <div className="mx-auto max-w-2xl">
      <p className="text-[17px] tracking-[-0.02em] text-neutral-400">Account</p>
      <h1 className="mt-1 text-4xl tracking-[-0.03em]">Profile</h1>
      <p className="mt-2 text-sm text-neutral-500">
        View your sign-in details and keep the information EPF Sarthi uses for this member.
      </p>

      <section className={cn(surface, 'mt-8 flex items-center gap-4 p-5')}>
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-neutral-950 text-lg text-white">
          {initials}
        </div>
        <div className="min-w-0">
          <p className="truncate text-lg tracking-[-0.02em]">
            {details.givenName || details.familyName
              ? `${details.givenName} ${details.familyName}`.trim()
              : account?.email ?? 'Signed in'}
          </p>
          <p className="truncate text-sm text-neutral-500">{account?.email}</p>
        </div>
      </section>

      <dl className={cn(surface, 'mt-4 grid gap-4 p-5 sm:grid-cols-2')}>
        <Info label="Email" value={account?.email ?? '—'} />
        <Info
          label="Email status"
          value={account?.verified ? 'Verified' : 'Not verified'}
        />
        <Info
          label="Saved claims"
          value={claimCount === null ? '—' : String(claimCount)}
        />
        <Info label="Member ID" value={account?.sub ? account.sub.slice(0, 8) : '—'} mono />
      </dl>

      <form onSubmit={handleSave} className={cn(surface, 'mt-4 space-y-4 p-5')}>
        <h2 className="text-xl tracking-[-0.02em]">Your details</h2>
        <p className="text-sm text-neutral-500">
          These stay with this browser for your account. They are not sent to EPFO.
        </p>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Given name">
            <input
              value={details.givenName}
              onChange={(e) => setDetails({ ...details, givenName: e.target.value })}
              autoComplete="given-name"
              className={insetField}
            />
          </Field>
          <Field label="Family name">
            <input
              value={details.familyName}
              onChange={(e) => setDetails({ ...details, familyName: e.target.value })}
              autoComplete="family-name"
              className={insetField}
            />
          </Field>
        </div>

        <Field label="Phone">
          <input
            type="tel"
            value={details.phone}
            onChange={(e) => setDetails({ ...details, phone: e.target.value })}
            autoComplete="tel"
            placeholder="Optional"
            className={insetField}
          />
        </Field>

        <Field label="City">
          <input
            value={details.city}
            onChange={(e) => setDetails({ ...details, city: e.target.value })}
            autoComplete="address-level2"
            placeholder="Optional"
            className={insetField}
          />
        </Field>

        <Field label="Preferred language">
          <select
            value={details.preferredLanguage}
            onChange={(e) =>
              setDetails({
                ...details,
                preferredLanguage: e.target.value === 'hi' ? 'hi' : 'en',
              })
            }
            className={insetField}
          >
            <option value="en">English</option>
            <option value="hi">हिन्दी</option>
          </select>
        </Field>

        {saveError && (
          <p role="alert" className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">
            {saveError}
          </p>
        )}
        {saved && <p className="text-sm text-emerald-700">Details saved.</p>}

        <button type="submit" className={primaryButton}>
          Save details
        </button>
      </form>

      <form onSubmit={handlePassword} className={cn(surface, 'mt-4 space-y-4 p-5')}>
        <h2 className="text-xl tracking-[-0.02em]">Password</h2>
        <p className="text-sm text-neutral-500">
          At least 8 characters, with an uppercase letter, a lowercase letter, and a number.
        </p>
        <Field label="Current password">
          <input
            type="password"
            required
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className={insetField}
          />
        </Field>
        <Field label="New password">
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className={insetField}
          />
        </Field>
        {passwordNotice && <p className="text-sm text-emerald-700">{passwordNotice}</p>}
        {passwordError && (
          <p role="alert" className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">
            {passwordError}
          </p>
        )}
        <button type="submit" disabled={passwordBusy} className={primaryButton}>
          {passwordBusy ? 'Updating…' : 'Update password'}
        </button>
      </form>

      <section className={cn(surface, 'mt-4 space-y-4 p-5')}>
        <h2 className="text-xl tracking-[-0.02em]">Delete my data</h2>
        <p className="text-sm leading-relaxed text-neutral-600">
          This removes your claims, uploads, analysis results, and this login. CloudWatch logs from
          the last 14 days may remain until they expire. Type DELETE to confirm.
        </p>
        <Field label="Confirmation">
          <input
            value={deleteConfirm}
            onChange={(e) => setDeleteConfirm(e.target.value)}
            placeholder="DELETE"
            className={insetField}
          />
        </Field>
        {deleteError && (
          <p role="alert" className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">
            {deleteError}
          </p>
        )}
        <button
          type="button"
          disabled={deleteBusy || deleteConfirm !== 'DELETE'}
          onClick={async () => {
            if (!account?.email) return;
            setDeleteError(null);
            setDeleteBusy(true);
            try {
              await deleteAccount();
              clearProfile(account.email);
              await cognito.deleteSignedInUser();
              signOut();
              navigate('/signin');
            } catch (err) {
              setDeleteError(err instanceof Error ? err.message : 'Could not delete this account.');
            } finally {
              setDeleteBusy(false);
            }
          }}
          className="rounded-full bg-red-700 px-5 py-2.5 text-sm text-white disabled:opacity-50"
        >
          {deleteBusy ? 'Deleting…' : 'Delete my data'}
        </button>
      </section>

      <div className="mt-6 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={() => {
            signOut();
            navigate('/signin');
          }}
          className="rounded-full border border-neutral-200 bg-white/70 px-5 py-2.5 text-sm text-neutral-800"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}

function Info({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className={cn('mt-1 text-sm text-neutral-900', mono && 'font-mono')}>{value}</dd>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-left">
      <span className="mb-1.5 block text-xs font-medium tracking-wide text-neutral-500">{label}</span>
      {children}
    </label>
  );
}
