/**
 * Build-time configuration.
 *
 * Every value here is public by design: the API base URL and the Cognito
 * pool/client ids are safe to ship in the bundle. No secret may be added to
 * this file — in particular the Gemini API key is read by Lambda from
 * Secrets Manager and never travels to the browser.
 */

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `Missing build-time env var ${name}. Copy .env.example to .env.local and fill it in.`,
    );
  }
  return value;
}

export const config = {
  apiBaseUrl: required('VITE_API_BASE_URL', import.meta.env.VITE_API_BASE_URL),
  cognitoUserPoolId: required(
    'VITE_COGNITO_USER_POOL_ID',
    import.meta.env.VITE_COGNITO_USER_POOL_ID,
  ),
  cognitoClientId: required('VITE_COGNITO_CLIENT_ID', import.meta.env.VITE_COGNITO_CLIENT_ID),
  awsRegion: required('VITE_AWS_REGION', import.meta.env.VITE_AWS_REGION),
  epfigmsUrl: import.meta.env.VITE_EPFIGMS_URL ?? 'https://epfigms.gov.in/',
} as const;
