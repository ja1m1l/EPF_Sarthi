import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import * as cognito from './cognito';

interface AuthState {
  email: string | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<{ confirmationRequired: boolean }>;
  confirmSignUp: (email: string, code: string) => Promise<void>;
  resendCode: (email: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [email, setEmail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    cognito.getSignedInEmail().then((value) => {
      if (!cancelled) {
        setEmail(value);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSignIn = useCallback(async (address: string, password: string) => {
    await cognito.signIn(address, password);
    setEmail(await cognito.getSignedInEmail());
  }, []);

  const handleSignUp = useCallback(async (address: string, password: string) => {
    const result = await cognito.signUp(address, password);
    return { confirmationRequired: !result.userConfirmed };
  }, []);

  const handleSignOut = useCallback(() => {
    cognito.signOut();
    setEmail(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      email,
      loading,
      signIn: handleSignIn,
      signUp: handleSignUp,
      confirmSignUp: cognito.confirmSignUp,
      resendCode: cognito.resendConfirmationCode,
      signOut: handleSignOut,
    }),
    [email, loading, handleSignIn, handleSignUp, handleSignOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside an AuthProvider');
  }
  return ctx;
}
