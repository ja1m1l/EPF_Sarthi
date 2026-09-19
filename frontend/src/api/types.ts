/**
 * Wire types mirroring the backend contracts.
 *
 * These follow the Lambda responses exactly; where the backend keeps two
 * states deliberately distinct (abstention vs failure, NOT_FOUND vs
 * CONTRADICTED), the types keep them distinct too rather than widening to a
 * boolean.
 */

export type ClaimType = 'FINAL_SETTLEMENT';

export type ClaimStatus = 'SUBMITTED' | 'PENDING' | 'UNDER_PROCESS' | 'REJECTED' | 'SETTLED';

export interface Claim {
  claimId: string;
  userId: string;
  claimType: ClaimType;
  claimDateIso: string;
  amountPaise: number;
  status: ClaimStatus;
  deficiencyRaisedDateIso: string | null;
  notes?: string | null;
  latestRunId?: string | null;
  latestRunStatus?: AnalysisRunStatus | null;
  createdAt: string;
  updatedAt: string;
}

export interface CreateClaimRequest {
  claimType: ClaimType;
  claimDate: string;
  amountRupees: string;
  status: ClaimStatus;
  deficiencyRaisedDate?: string;
  notes?: string;
}

/** A run is terminal in exactly one of these four states — never fewer. */
export type AnalysisRunStatus =
  | 'RUNNING'
  | 'COMPLETED'
  | 'COMPLETED_WITH_ABSTENTION'
  | 'FAILED_INFRASTRUCTURE'
  | 'FAILED_VALIDATION';

export type SlaStatus = 'WITHIN' | 'APPROACHING' | 'OVERDUE';

export type EvidenceVerdict = 'CONFIRMED' | 'NOT_FOUND' | 'CONTRADICTED';

export interface CitedSource {
  chunkId: string;
  sourceUrl: string;
  sourceTitle: string;
  retrievedOn: string;
  authority: string;
}

export interface RulesDecision {
  applicable: boolean;
  timelineDays: number | null;
  timelineBasis: 'CALENDAR' | 'WORKING' | null;
  charterTargetDays: number | null;
  citedChunkIds: string[];
  citedSourceUrls: string[];
  citedSources?: CitedSource[];
  quotedSpan: string;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  abstainReason: string | null;
  stubbed?: boolean;
}

/** Skipped when the Rules Agent abstained — there is no timeline to compute. */
export interface SlaResultSkipped {
  skipped: true;
  reason: string;
}

export interface SlaResultComputed {
  skipped?: false;
  deadlineDateIso: string;
  elapsedDays: number;
  remainingDays: number;
  clockStartDateIso: string;
  status: SlaStatus;
  explanation: string;
  priorElapsedDays: number | null;
}

export type SlaResult = SlaResultSkipped | SlaResultComputed;

export interface EvidenceSourceRef {
  documentId: string;
  excerpt: string;
}

export interface EvidenceCheck {
  checkId: string;
  label: string;
  verdict: EvidenceVerdict;
  sourceRef: EvidenceSourceRef | null;
  note: string;
}

export interface EvidenceReport {
  documents: unknown[];
  summary: string;
  checks: EvidenceCheck[];
  stubbed?: boolean;
}

export interface GrievanceDraft {
  draftText: string;
  stubbed?: boolean;
}

export interface FailureDetail {
  errorType: string;
  errorMessage: string;
  cause: string;
}

export interface AnalysisRun {
  claimId: string;
  runId: string;
  correlationId: string;
  status: AnalysisRunStatus;
  startedAt: string;
  finishedAt?: string;
  rulesAgentOutputJson?: RulesDecision;
  slaAgentOutputJson?: SlaResult;
  evidenceAgentOutputJson?: EvidenceReport;
  grievanceAgentOutputJson?: GrievanceDraft;
  failureDetail?: FailureDetail;
}

export interface StartAnalysisResponse {
  runId: string;
  correlationId: string;
  status: 'RUNNING';
}

export interface PresignResponse {
  documentId: string;
  url: string;
  uploadUrl: string;
  postUrl: string;
  fields: Record<string, string>;
  expiresIn: number;
}

export type DocumentStatus = 'PROCESSING' | 'EXTRACTED' | 'NEEDS_MANUAL_ENTRY';

export interface ClaimDocument {
  documentId: string;
  claimId: string;
  userId: string;
  status: DocumentStatus;
  contentType: string;
  s3Key: string;
  extractionMethod?: 'PDF_TEXT_LAYER' | 'VLM_TRANSCRIPTION';
  needsManualEntryReason?: string;
  charCount?: number;
  processedAt?: string;
}

export interface ApiErrorBody {
  error?: {
    code?: string;
    field?: string;
    message?: string;
  };
}
