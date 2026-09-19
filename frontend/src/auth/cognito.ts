/**
 * Cognito user pool access via amazon-cognito-identity-js.
 *
 * Token choice: the HTTP API's JWT authorizer is configured with
 * `audience: <app client id>`, and only the ID token carries an `aud` claim
 * matching that. The access token carries `client_id` instead and is
 * rejected. Every authenticated request therefore sends the ID token.
 */

import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  CognitoUserSession,
  type ISignUpResult,
} from 'amazon-cognito-identity-js';

import { config } from '../config';

const userPool = new CognitoUserPool({
  UserPoolId: config.cognitoUserPoolId,
  ClientId: config.cognitoClientId,
});

export class AuthError extends Error {
  readonly code: string;

  constructor(message: string, code = 'AUTH_ERROR') {
    super(message);
    this.name = 'AuthError';
    this.code = code;
  }
}

function toAuthError(err: unknown): AuthError {
  if (err && typeof err === 'object' && 'message' in err) {
    const e = err as { message?: string; code?: string; name?: string };
    return new AuthError(e.message ?? 'Authentication failed', e.code ?? e.name ?? 'AUTH_ERROR');
  }
  return new AuthError('Authentication failed');
}

function cognitoUser(email: string): CognitoUser {
  return new CognitoUser({ Username: email, Pool: userPool });
}

export function getCurrentUser(): CognitoUser | null {
  return userPool.getCurrentUser();
}

export function signIn(email: string, password: string): Promise<CognitoUserSession> {
  return new Promise((resolve, reject) => {
    cognitoUser(email).authenticateUser(
      new AuthenticationDetails({ Username: email, Password: password }),
      {
        onSuccess: (session) => resolve(session),
        onFailure: (err) => reject(toAuthError(err)),
        newPasswordRequired: () =>
          reject(
            new AuthError(
              'This account must set a new password before signing in.',
              'NEW_PASSWORD_REQUIRED',
            ),
          ),
      },
    );
  });
}

export function signUp(email: string, password: string): Promise<ISignUpResult> {
  return new Promise((resolve, reject) => {
    userPool.signUp(email, password, [], [], (err, result) => {
      if (err || !result) {
        reject(toAuthError(err));
        return;
      }
      resolve(result);
    });
  });
}

export function confirmSignUp(email: string, code: string): Promise<void> {
  return new Promise((resolve, reject) => {
    cognitoUser(email).confirmRegistration(code, true, (err) => {
      if (err) {
        reject(toAuthError(err));
        return;
      }
      resolve();
    });
  });
}

export function resendConfirmationCode(email: string): Promise<void> {
  return new Promise((resolve, reject) => {
    cognitoUser(email).resendConfirmationCode((err) => {
      if (err) {
        reject(toAuthError(err));
        return;
      }
      resolve();
    });
  });
}

export function signOut(): void {
  getCurrentUser()?.signOut();
}

/**
 * Return a valid ID token, refreshing it if the cached session has expired.
 * Resolves null when nobody is signed in.
 */
export function getIdToken(): Promise<string | null> {
  return new Promise((resolve) => {
    const user = getCurrentUser();
    if (!user) {
      resolve(null);
      return;
    }

    // getSession refreshes automatically when the session is expired but the
    // refresh token is still valid.
    user.getSession((err: Error | null, session: CognitoUserSession | null) => {
      if (err || !session || !session.isValid()) {
        resolve(null);
        return;
      }
      resolve(session.getIdToken().getJwtToken());
    });
  });
}

export function getSignedInEmail(): Promise<string | null> {
  return new Promise((resolve) => {
    const user = getCurrentUser();
    if (!user) {
      resolve(null);
      return;
    }
    user.getSession((err: Error | null, session: CognitoUserSession | null) => {
      if (err || !session?.isValid()) {
        resolve(null);
        return;
      }
      const payload = session.getIdToken().decodePayload() as { email?: string };
      resolve(payload.email ?? user.getUsername());
    });
  });
}
