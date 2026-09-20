import { Link } from 'react-router-dom';

import { surface } from '@/components/ui';
import { cn } from '@/lib/utils';

export function PrivacyHelp() {
  return (
    <article className="mx-auto max-w-2xl pb-10">
      <p className="text-[17px] tracking-[-0.02em] text-neutral-400">EPF Sarthi</p>
      <h1 className="mt-1 text-4xl tracking-[-0.03em]">Privacy and help</h1>
      <p className="mt-3 text-sm leading-relaxed text-neutral-600">
        This page explains what the site does, what it stores, and how to remove your data. It is
        not legal advice and EPF Sarthi is not EPFO.
      </p>

      <section className={cn(surface, 'mt-8 space-y-3 p-5')}>
        <h2 className="text-xl tracking-[-0.02em]">How this works</h2>
        <p className="text-sm leading-relaxed text-neutral-600">
          You enter a PF Final Settlement claim and may upload screenshots. Analysis compares those
          dates with published EPFO rule text, shows a timeline, lists evidence checks, and drafts a
          grievance for you to copy. Nothing is submitted to EPFO or EPFiGMS automatically.
        </p>
        <p className="text-sm leading-relaxed text-neutral-600">
          “No applicable rule” means we did not match a published timeline. “Analysis could not be
          completed” means a system or model failure, not an abstention.
        </p>
      </section>

      <section className={cn(surface, 'mt-4 space-y-3 p-5')}>
        <h2 className="text-xl tracking-[-0.02em]">What we store</h2>
        <p className="text-sm leading-relaxed text-neutral-600">
          Your login is held in Amazon Cognito (email and password). Claims, analysis results, and
          uploaded files are stored in your AWS account’s EPF Sarthi tables and a private document
          bucket. Uploaded files expire after 30 days. Analysis rows expire after 90 days. CloudWatch
          logs retain 14 days.
        </p>
        <p className="text-sm leading-relaxed text-neutral-600">
          Name, phone, city, and language on the Profile page are saved only in this browser, not in
          Cognito.
        </p>
      </section>

      <section className={cn(surface, 'mt-4 space-y-3 p-5')}>
        <h2 className="text-xl tracking-[-0.02em]">Google Gemini</h2>
        <p className="text-sm leading-relaxed text-neutral-600">
          Claim text, retrieved public rule chunks, evidence text, grievance drafts, and scans
          without a usable text layer are sent to the Google Gemini API. That processing is not
          limited to AWS. Grievance drafting redacts UAN-like and bank-like numbers in code before
          that call. Uploaded images may still be sent for transcription before text exists to
          redact.
        </p>
      </section>

      <section className={cn(surface, 'mt-4 space-y-3 p-5')}>
        <h2 className="text-xl tracking-[-0.02em]">Delete your data</h2>
        <p className="text-sm leading-relaxed text-neutral-600">
          Sign in, open Profile, and choose Delete my data. That removes claims, documents, analysis
          runs, and the login for this account. CloudWatch logs from the previous 14 days may still
          exist until they expire.
        </p>
        <p className="text-sm leading-relaxed text-neutral-600">
          Forgot your password? Use Forgot password on the sign-in page. We email a code to the
          address on the account.
        </p>
      </section>

      <p className="mt-6 text-sm text-neutral-500">
        <Link to="/signin" className="underline">
          Back to sign in
        </Link>
      </p>
    </article>
  );
}
