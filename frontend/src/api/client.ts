/**
 * Thin fetch wrapper over the EPF Sentinel HTTP API.
 *
 * Every call attaches the Cognito ID token. The backend derives userId from
 * the token's `sub` claim alone, so no request here ever sends a userId.
 */

import { getIdToken } from '../auth/cognito';
import { config } from '../config';
import type {
  AnalysisRun,
  Claim,
  ClaimDocument,
  CreateClaimRequest,
  PresignResponse,
  StartAnalysisResponse,
  ApiErrorBody,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly field?: string;

  constructor(status: number, code: string, message: string, field?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.field = field;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getIdToken();
  if (!token) {
    throw new ApiError(401, 'NOT_SIGNED_IN', 'Your session has expired. Please sign in again.');
  }

  const response = await fetch(`${config.apiBaseUrl}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: token,
      ...(init.headers ?? {}),
    },
  });

  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const err = (body as ApiErrorBody | null)?.error;
    throw new ApiError(
      response.status,
      err?.code ?? `HTTP_${response.status}`,
      err?.message ?? `Request failed with status ${response.status}`,
      err?.field,
    );
  }

  return body as T;
}

export function createClaim(payload: CreateClaimRequest): Promise<Claim> {
  return request<Claim>('/claims', { method: 'POST', body: JSON.stringify(payload) });
}

export function listClaims(): Promise<{ claims: Claim[]; count: number }> {
  return request<{ claims: Claim[]; count: number }>('/claims');
}

export function getClaim(claimId: string): Promise<Claim> {
  return request<Claim>(`/claims/${claimId}`);
}

export function startAnalysis(claimId: string): Promise<StartAnalysisResponse> {
  return request<StartAnalysisResponse>(`/claims/${claimId}/analyze`, { method: 'POST' });
}

export function getRun(claimId: string, runId: string): Promise<AnalysisRun> {
  return request<AnalysisRun>(`/claims/${claimId}/runs/${runId}`);
}

export function listRuns(claimId: string): Promise<{ runs: AnalysisRun[] }> {
  return request<{ runs: AnalysisRun[] }>(`/claims/${claimId}/runs`);
}

export function getDocument(
  claimId: string,
  documentId: string,
  includeText = false,
): Promise<ClaimDocument> {
  const query = includeText ? '?includeText=true' : '';
  return request<ClaimDocument>(`/claims/${claimId}/documents/${documentId}${query}`);
}

function presignUpload(
  claimId: string,
  contentType: string,
  documentKind?: string,
): Promise<PresignResponse> {
  return request<PresignResponse>(`/claims/${claimId}/documents`, {
    method: 'POST',
    body: JSON.stringify({
      contentType,
      ...(documentKind ? { documentKind } : {}),
    }),
  });
}

/**
 * Presign, then PUT the file straight to S3.
 *
 * The bytes never pass through API Gateway. The 10 MB cap and content-type
 * allow-list are enforced by S3 from the presign conditions, so the check
 * below is a courtesy to the user rather than the control.
 */
export async function uploadDocument(
  claimId: string,
  file: File,
  documentKind?: string,
): Promise<string> {
  const presigned = await presignUpload(claimId, file.type, documentKind);

  const response = await fetch(presigned.uploadUrl, {
    method: 'PUT',
    headers: { 'Content-Type': file.type },
    body: file,
  });

  if (!response.ok) {
    throw new ApiError(response.status, 'UPLOAD_FAILED', `Upload failed for ${file.name}.`);
  }

  return presigned.documentId;
}
