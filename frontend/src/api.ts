const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch (err) {
    throw new Error(
      `Нет связи с API (${API_BASE}). Проверьте, что backend запущен. ${err instanceof Error ? err.message : ''}`,
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail ?? JSON.stringify(data);
    } catch {
      /* ignore */
    }
    if ((!detail || detail === 'Service Unavailable') && res.status === 503) {
      detail =
        'Backend временно недоступен (перезагрузка uvicorn). Подождите 1–2 сек и повторите.';
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type CampaignStatus = 'draft' | 'generating' | 'ready';
export type DraftStatus = 'pending' | 'generating' | 'ready' | 'error' | 'edited';
export type TemplateStatus = 'draft' | 'ready';

export interface Campaign {
  id: number;
  name: string;
  base_prompt: string;
  llm_provider: string;
  llm_model?: string | null;
  use_web_search: boolean;
  use_segment_templates?: boolean;
  segment_field?: string | null;
  status: CampaignStatus;
  recipient_count: number;
  draft_count: number;
  rag_file_count: number;
  created_at?: string;
}

export interface SegmentField {
  key: string;
  label: string;
  non_empty_count: number;
  sample_values: string[];
}

export interface Zone {
  name: string;
  hint?: string;
}

export interface SegmentTemplate {
  id: number;
  segment_id: number;
  variant_key: string;
  subject_template: string;
  body_template: string;
  zones: Zone[];
  status: TemplateStatus;
}

export interface Segment {
  id: number;
  campaign_id: number;
  value: string;
  label: string;
  recipient_count: number;
  template?: SegmentTemplate | null;
}

export interface LLMModel {
  id: string;
  name: string;
  description: string;
  context_length?: number | null;
  supports_tools: boolean;
  match_reasons: string[];
  provider: string;
  price_prompt_per_1m?: number | null;
  price_completion_per_1m?: number | null;
  price_label?: string;
}

export interface RagFile {
  id: number;
  campaign_id: number;
  filename: string;
  content_type?: string | null;
  source_type?: string;
  source_url?: string | null;
  char_count: number;
  created_at?: string | null;
}

export interface CostEstimate {
  campaign_id: number;
  recipient_count: number;
  model_id?: string | null;
  price_label: string;
  estimated_prompt_tokens: number;
  estimated_completion_tokens: number;
  estimated_digest_tokens: number;
  estimated_cost_usd: number;
  notes: string[];
}

export interface Recipient {
  id: number;
  email: string;
  name?: string | null;
  company_name: string;
  industry?: string | null;
  description?: string | null;
  website_url?: string | null;
  extra_fields?: Record<string, string> | null;
}

export interface Draft {
  id: number;
  campaign_id: number;
  recipient_id: number;
  subject?: string | null;
  body_text?: string | null;
  body_html?: string | null;
  status: DraftStatus;
  error?: string | null;
  recipient?: Recipient | null;
  updated_at?: string | null;
}

export interface SegmentProgress {
  segment_id?: number | null;
  value: string;
  label: string;
  total: number;
  pending: number;
  generating: number;
  ready: number;
  error: number;
  edited: number;
}

export interface CampaignStatusOut {
  campaign_id: number;
  status: CampaignStatus;
  total: number;
  pending: number;
  generating: number;
  ready: number;
  error: number;
  edited: number;
  worker_alive?: boolean;
  use_segment_templates?: boolean;
  segments?: SegmentProgress[];
}

export interface Provider {
  type: string;
  display_name: string;
  enabled: boolean;
  available: boolean;
  description: string;
}

export interface ImportResult {
  imported: number;
  skipped_duplicates: number;
  skipped_invalid: number;
  recipients: Recipient[];
}

export type CampaignPayload = {
  name?: string;
  base_prompt?: string;
  llm_provider?: string;
  llm_model?: string | null;
  use_web_search?: boolean;
};

export const api = {
  listCampaigns: () => request<Campaign[]>('/api/campaigns'),
  createCampaign: (body: {
    name: string;
    base_prompt: string;
    llm_provider?: string;
    llm_model?: string | null;
    use_web_search?: boolean;
  }) =>
    request<Campaign>('/api/campaigns', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  getCampaign: (id: number) => request<Campaign>(`/api/campaigns/${id}`),
  updateCampaign: (id: number, body: CampaignPayload) =>
    request<Campaign>(`/api/campaigns/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  listModels: () => request<LLMModel[]>('/api/models?capability=search_rag'),
  listRecipients: (id: number) => request<Recipient[]>(`/api/campaigns/${id}/recipients`),
  importRecipients: async (id: number, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<ImportResult>(`/api/campaigns/${id}/recipients/import`, {
      method: 'POST',
      body: form,
    });
  },
  listRagFiles: (id: number) => request<RagFile[]>(`/api/campaigns/${id}/rag`),
  uploadRagFiles: async (id: number, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    // #region agent log
    fetch('http://127.0.0.1:7869/ingest/f126a795-6571-4f70-980d-f352154f7ca3',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'bd0245'},body:JSON.stringify({sessionId:'bd0245',location:'api.ts:uploadRagFiles',message:'upload_request',data:{id,count:files.length,names:files.map(f=>f.name),sizes:files.map(f=>f.size),types:files.map(f=>f.type)},timestamp:Date.now(),hypothesisId:'B',runId:'pre-fix'})}).catch(()=>{});
    // #endregion
    try {
      return await request<RagFile[]>(`/api/campaigns/${id}/rag`, {
        method: 'POST',
        body: form,
      });
    } catch (err) {
      // #region agent log
      fetch('http://127.0.0.1:7869/ingest/f126a795-6571-4f70-980d-f352154f7ca3',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'bd0245'},body:JSON.stringify({sessionId:'bd0245',location:'api.ts:uploadRagFiles',message:'upload_error',data:{id,error:err instanceof Error?err.message:String(err)},timestamp:Date.now(),hypothesisId:'C',runId:'pre-fix'})}).catch(()=>{});
      // #endregion
      throw err;
    }
  },
  addRagWebsite: (id: number, url: string) =>
    request<RagFile>(`/api/campaigns/${id}/rag/url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }),
  deleteRagFile: (campaignId: number, fileId: number) =>
    request<{ ok: boolean }>(`/api/campaigns/${campaignId}/rag/${fileId}`, {
      method: 'DELETE',
    }),
  deleteCampaign: (id: number) =>
    request<{ ok: boolean; deleted: number[] }>(`/api/campaigns/${id}`, {
      method: 'DELETE',
    }),
  deleteCampaigns: (ids: number[]) =>
    request<{ ok: boolean; deleted: number[]; requested: number[] }>('/api/campaigns/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    }),
  costEstimate: (id: number) => request<CostEstimate>(`/api/campaigns/${id}/cost-estimate`),
  listSegmentFields: (id: number) =>
    request<SegmentField[]>(`/api/campaigns/${id}/segment-fields`),
  listSegments: (id: number) => request<Segment[]>(`/api/campaigns/${id}/segments`),
  updateSegmentsConfig: (
    id: number,
    body: { use_segment_templates: boolean; segment_field?: string | null },
  ) =>
    request<Segment[]>(`/api/campaigns/${id}/segments/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  generateSegmentTemplates: (id: number) =>
    request<Segment[]>(`/api/campaigns/${id}/segments/generate-templates`, {
      method: 'POST',
    }),
  updateSegmentTemplate: (
    campaignId: number,
    segmentId: number,
    body: {
      subject_template?: string;
      body_template?: string;
      zones?: Zone[];
      status?: TemplateStatus;
    },
  ) =>
    request<SegmentTemplate>(`/api/campaigns/${campaignId}/segments/${segmentId}/template`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  generate: (id: number, enrich_websites: boolean) =>
    request<{ ok: boolean }>(`/api/campaigns/${id}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enrich_websites }),
    }),
  stopGenerate: (id: number) =>
    request<{ ok: boolean; status: string; message?: string }>(
      `/api/campaigns/${id}/generate/stop`,
      { method: 'POST' },
    ),
  status: (id: number) => request<CampaignStatusOut>(`/api/campaigns/${id}/status`),
  listDrafts: (id: number) => request<Draft[]>(`/api/campaigns/${id}/drafts`),
  updateDraft: (id: number, body: { subject?: string; body_text?: string }) =>
    request<Draft>(`/api/drafts/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  regenerateDraft: (id: number, enrich_websites = false) =>
    request<Draft>(`/api/drafts/${id}/regenerate?enrich_websites=${enrich_websites}`, {
      method: 'POST',
    }),
  exportUrl: (id: number, format: 'csv' | 'json') =>
    `${API_BASE}/api/campaigns/${id}/export?format=${format}`,
  listProviders: () => request<Provider[]>('/api/providers'),
};
