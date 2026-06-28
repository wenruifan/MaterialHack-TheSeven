import type {
  BoltzApiAuthStatus,
  CreateRunPayload,
  JobPayload,
  LatestRunResponse,
  LoopDetail,
  MemorySnapshot,
  ObjectiveParameters,
  RunResponse,
  WorkbenchEvent
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function parseObjective(objective: string): Promise<ObjectiveParameters> {
  return request<ObjectiveParameters>("/api/objectives/parse", {
    method: "POST",
    body: JSON.stringify({ objective })
  });
}

export function createRun(payload: CreateRunPayload): Promise<RunResponse> {
  return request<RunResponse>("/api/runs", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function getLatestRun(): Promise<LatestRunResponse> {
  return request<LatestRunResponse>("/api/runs/latest");
}

export function getBoltzApiAuthStatus(): Promise<BoltzApiAuthStatus> {
  return request<BoltzApiAuthStatus>("/api/boltz-api/auth");
}

export function setBoltzApiKey(apiKey: string): Promise<BoltzApiAuthStatus> {
  return request<BoltzApiAuthStatus>("/api/boltz-api/auth", {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey })
  });
}

export function clearBoltzApiKey(): Promise<BoltzApiAuthStatus> {
  return request<BoltzApiAuthStatus>("/api/boltz-api/auth", {
    method: "DELETE"
  });
}

export function getMemory(runId: string): Promise<MemorySnapshot> {
  return request<MemorySnapshot>(`/api/runs/${runId}/memory`);
}

export function getLoopDetail(runId: string, loopId: string): Promise<LoopDetail> {
  return request<LoopDetail>(`/api/runs/${runId}/loops/${loopId}`);
}

export function runLoops(runId: string, loopCount: number, startLoopId?: string | null): Promise<RunResponse> {
  return request<RunResponse>(`/api/runs/${runId}/loops`, {
    method: "POST",
    body: JSON.stringify({ loop_count: loopCount, start_loop_id: startLoopId ?? null })
  });
}

export function rollbackLoop(runId: string, loopId: string, reason: string, actor = "human"): Promise<unknown> {
  return request<unknown>(`/api/runs/${runId}/rollback`, {
    method: "POST",
    body: JSON.stringify({ loop_id: loopId, reason, actor })
  });
}

export function rejectLoop(runId: string, loopId: string, reason: string, actor = "Codex"): Promise<unknown> {
  return request<unknown>(`/api/runs/${runId}/agent-loops/${loopId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason, actor })
  });
}

export function getJob(jobId: string): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${jobId}`);
}

export function createRunEventSource(runId: string): EventSource {
  return new EventSource(`/api/runs/${runId}/events`);
}

export function decodeEvent(message: MessageEvent<string>): WorkbenchEvent {
  return JSON.parse(message.data) as WorkbenchEvent;
}
