const STORAGE_PREFIX = 'epf-sarthi-profile:';

export interface ProfileDetails {
  givenName: string;
  familyName: string;
  phone: string;
  city: string;
  preferredLanguage: 'en' | 'hi';
}

export const emptyProfile = (): ProfileDetails => ({
  givenName: '',
  familyName: '',
  phone: '',
  city: '',
  preferredLanguage: 'en',
});

function keyFor(email: string): string {
  return `${STORAGE_PREFIX}${email.trim().toLowerCase()}`;
}

export function loadProfile(email: string): ProfileDetails {
  if (!email) return emptyProfile();
  try {
    const raw = localStorage.getItem(keyFor(email));
    if (!raw) return emptyProfile();
    const parsed = JSON.parse(raw) as Partial<ProfileDetails>;
    return {
      givenName: String(parsed.givenName ?? ''),
      familyName: String(parsed.familyName ?? ''),
      phone: String(parsed.phone ?? ''),
      city: String(parsed.city ?? ''),
      preferredLanguage: parsed.preferredLanguage === 'hi' ? 'hi' : 'en',
    };
  } catch {
    return emptyProfile();
  }
}

export function saveProfile(email: string, details: ProfileDetails): void {
  localStorage.setItem(keyFor(email), JSON.stringify(details));
}

export function clearProfile(email: string): void {
  if (!email) return;
  localStorage.removeItem(keyFor(email));
}
