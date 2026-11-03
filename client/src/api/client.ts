import type { components } from "./schema";

type Schemas = components["schemas"];
export type SessionOut = Schemas["SessionOut"];
export type CapabilitiesOut = Schemas["CapabilitiesOut"];
export type DemoOut = Schemas["DemoOut"];
export type ClipOut = Schemas["ClipOut"];
export type FrameOut = Schemas["FrameOut"];
export type AnalysisOut = Schemas["AnalysisOut"];
export type EventOut = Schemas["EventOut"];
export type CandidateOut = Schemas["CandidateOut"];
export type EvidenceOut = Schemas["EvidenceOut"];
export type AssessmentOut = Schemas["AssessmentOut"];
export type EntityOut = Schemas["EntityOut"];
export type ObservationOut = Schemas["ObservationOut"];
export type ObservationsPage = Schemas["ObservationsPage"];
export type VisibilityOut = Schemas["VisibilityOut"];
export type VisibilitySpanOut = Schemas["VisibilitySpanOut"];
export type InvestigationOut = Schemas["InvestigationOut"];
export type AnswerSentenceOut = Schemas["AnswerSentenceOut"];
export type JobOut = Schemas["JobOut"];
export type JobAccepted = Schemas["JobAccepted"];
export type ExportOut = Schemas["ExportOut"];
export type VerifyOut = Schemas["VerifyOut"];
export type UsageOut = Schemas["UsageOut"];
export type RunMode = AnalysisOut["run_mode"];
export type AnalysisCreate = Schemas["AnalysisCreate"];

export interface JobEvent {
  job_id: string;
  seq: number;
  type: string;
  stage: string | null;
  summary: string;
  elapsed_ms: number;
  mode: RunMode | null;
  created_at: string;
  data: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public retryable = false,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const genericError = { code: `HTTP_${response.status}`, message: "The request failed.", retryable: false };
    const payload = await response.json().catch(() => null);
    const error = payload?.error ?? genericError;
    throw new ApiError(error.code, error.message, response.status, error.retryable);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function idempotencyKey(prefix: string): string {
  const random = crypto.getRandomValues(new Uint8Array(12));
  return `${prefix}-${Array.from(random, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

const V1 = "/api/v1";

export const api = {
  async session(accessCode?: string): Promise<SessionOut> {
    const session = await request<SessionOut>("POST", `${V1}/session`, { access_code: accessCode ?? null });
    csrfToken = session.csrf_token;
    return session;
  },
  capabilities: () => request<CapabilitiesOut>("GET", `${V1}/capabilities`),
  demos: () => request<DemoOut[]>("GET", `${V1}/demos`),
  clips: () => request<ClipOut[]>("GET", `${V1}/clips`),
  clip: (id: string) => request<ClipOut>("GET", `${V1}/clips/${id}`),
  deleteClip: (id: string) => request<void>("DELETE", `${V1}/clips/${id}`),
  frame: (id: string, tMs: number) => request<FrameOut>("GET", `${V1}/clips/${id}/frame?t_ms=${Math.max(0, Math.round(tMs))}`),
  clipAnalyses: (id: string) => request<AnalysisOut[]>("GET", `${V1}/clips/${id}/analyses`),
  createAnalysis: (clipId: string, body: AnalysisCreate) => request<JobAccepted>("POST", `${V1}/clips/${clipId}/analyses`, body),
  bindSelection: (analysisId: string, selectionId: string) =>
    request<JobAccepted>("POST", `${V1}/analyses/${analysisId}/selection`, {
      selection_id: selectionId,
      idempotency_key: idempotencyKey("select"),
    }),
  analysis: (id: string) => request<AnalysisOut>("GET", `${V1}/analyses/${id}`),
  observations: (id: string, t0: number, t1: number) =>
    request<ObservationsPage>("GET", `${V1}/analyses/${id}/observations?t0_ms=${t0}&t1_ms=${t1}`),
  visibility: (id: string) => request<VisibilityOut>("GET", `${V1}/analyses/${id}/visibility`),
  ask: (analysisId: string, question: string) =>
    request<JobAccepted>("POST", `${V1}/analyses/${analysisId}/questions`, {
      question,
      object_id: "O1",
      idempotency_key: idempotencyKey("question"),
    }),
  investigations: (analysisId: string) => request<InvestigationOut[]>("GET", `${V1}/analyses/${analysisId}/investigations`),
  job: (id: string) => request<JobOut>("GET", `${V1}/jobs/${id}`),
  cancel: (id: string) => request<JobOut>("POST", `${V1}/jobs/${id}/cancel`),
  createExport: (analysisId: string) =>
    request<JobAccepted>("POST", `${V1}/analyses/${analysisId}/exports`, { idempotency_key: idempotencyKey("export") }),
  exports: (analysisId: string) => request<ExportOut[]>("GET", `${V1}/analyses/${analysisId}/exports`),
  verify: (exportId: string, expectedHead?: string) =>
    request<VerifyOut>("POST", `${V1}/exports/${exportId}/verify`, { expected_head: expectedHead || null }),
  upload(file: File, displayName: string, onProgress: (fraction: number) => void): Promise<ClipOut> {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("consent", "true");
      form.append("display_name", displayName);
      form.append("file", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${V1}/clips`);
      if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () => {
        const parsed = (() => {
          try {
            return JSON.parse(xhr.responseText);
          } catch {
            return null;
          }
        })();
        if (xhr.status === 201) resolve(parsed as ClipOut);
        else reject(new ApiError(parsed?.error?.code ?? `HTTP_${xhr.status}`, parsed?.error?.message ?? "Upload failed.", xhr.status));
      };
      xhr.onerror = () => reject(new ApiError("NETWORK", "The upload was interrupted.", 0, true));
      xhr.send(form);
    });
  },
};
