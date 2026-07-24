import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import {
  api,
  type Campaign,
  type CampaignStatusOut,
  type CostEstimate,
  type Draft,
  type DraftStatus,
  type LLMModel,
  type Provider,
  type RagFile,
  type Recipient,
  type Segment,
  type SegmentField,
} from '../api';
import { CampaignProgressPanel } from '../components/CampaignProgressPanel';

function modelOptionLabel(m: LLMModel): string {
  const price = m.price_label ? ` — ${m.price_label}` : '';
  return `${m.name}${price}`;
}

function highlightZones(text: string): string {
  return text.replace(
    /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g,
    '<mark class="zone-mark">{{$1}}</mark>',
  );
}

const STATUS_RU: Record<string, string> = {
  draft: 'черновик',
  generating: 'генерация',
  ready: 'готово',
};

export function CampaignDetailPage() {
  const { id } = useParams();
  const location = useLocation();
  const campaignId = Number(id);

  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [recipients, setRecipients] = useState<Recipient[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [status, setStatus] = useState<CampaignStatusOut | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<LLMModel[]>([]);
  const [ragFiles, setRagFiles] = useState<RagFile[]>([]);
  const [ragUrl, setRagUrl] = useState('');
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);
  const [enrichWebsites, setEnrichWebsites] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [modelId, setModelId] = useState('');
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [useSegmentTemplates, setUseSegmentTemplates] = useState(false);
  const [segmentField, setSegmentField] = useState('');
  const [segmentFields, setSegmentFields] = useState<SegmentField[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [openSegmentId, setOpenSegmentId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createWarnings, setCreateWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [templatesBusy, setTemplatesBusy] = useState(false);
  const [importInfo, setImportInfo] = useState<string | null>(null);
  const [progressFilter, setProgressFilter] = useState<DraftStatus | 'all'>('all');
  const [genStartedAt, setGenStartedAt] = useState<number | null>(null);
  const [stopping, setStopping] = useState(false);
  const [tab, setTab] = useState<
    'settings' | 'recipients' | 'segments' | 'rag' | 'generate' | 'drafts' | 'export'
  >('settings');
  const wasGenerating = useRef(false);

  useEffect(() => {
    const state = location.state as { createWarnings?: string[] } | null;
    if (state?.createWarnings?.length) {
      setCreateWarnings(state.createWarnings);
      // #region agent log
      fetch('http://127.0.0.1:7869/ingest/f126a795-6571-4f70-980d-f352154f7ca3',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'bd0245'},body:JSON.stringify({sessionId:'bd0245',location:'CampaignDetailPage.tsx',message:'create_warnings_shown',data:{warnings:state.createWarnings},timestamp:Date.now(),hypothesisId:'A',runId:'post-fix'})}).catch(()=>{});
      // #endregion
    }
  }, [location.state]);

  const refresh = useCallback(async () => {
    const [c, r, d, s, p, m, rag, cost, fields, segs] = await Promise.all([
      api.getCampaign(campaignId),
      api.listRecipients(campaignId),
      api.listDrafts(campaignId),
      api.status(campaignId),
      api.listProviders(),
      api.listModels(),
      api.listRagFiles(campaignId),
      api.costEstimate(campaignId).catch(() => null),
      api.listSegmentFields(campaignId).catch(() => [] as SegmentField[]),
      api.listSegments(campaignId).catch(() => [] as Segment[]),
    ]);
    setCampaign(c);
    setPrompt(c.base_prompt);
    setModelId(c.llm_model || '');
    setUseWebSearch(c.use_web_search);
    setUseSegmentTemplates(!!c.use_segment_templates);
    setSegmentField(c.segment_field || '');
    setRecipients(r);
    setDrafts(d);
    setStatus(s);
    setProviders(p);
    setModels(m);
    setRagFiles(rag);
    setEstimate(cost);
    setSegmentFields(fields);
    setSegments(segs);
  }, [campaignId]);

  const refreshProgress = useCallback(async () => {
    const [c, d, s] = await Promise.all([
      api.getCampaign(campaignId),
      api.listDrafts(campaignId),
      api.status(campaignId),
    ]);
    setCampaign(c);
    setDrafts(d);
    setStatus(s);
  }, [campaignId]);

  useEffect(() => {
    if (!Number.isFinite(campaignId)) return;
    void refresh().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [campaignId, refresh]);

  useEffect(() => {
    const generating = campaign?.status === 'generating';
    if (generating && !wasGenerating.current) {
      setGenStartedAt((prev) => prev ?? Date.now());
      setProgressFilter('all');
      setTab('drafts');
    }
    wasGenerating.current = !!generating;
    if (!generating) {
      setStopping(false);
      return;
    }

    const timer = window.setInterval(() => {
      void refreshProgress().catch(() => undefined);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [campaign?.status, refreshProgress]);

  const onSavePrompt = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateCampaign(campaignId, {
        base_prompt: prompt,
        llm_model: modelId || null,
        use_web_search: useWebSearch,
        llm_provider: 'openrouter',
      });
      setCampaign(updated);
      setEstimate(await api.costEstimate(campaignId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onImport = async (file: File | null) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setImportInfo(null);
    try {
      const result = await api.importRecipients(campaignId, file);
      setImportInfo(
        `Импортировано: ${result.imported}. Дубликаты: ${result.skipped_duplicates}. Невалидные: ${result.skipped_invalid}.`,
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onUploadRag = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await api.uploadRagFiles(campaignId, Array.from(files));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onAddRagUrl = async () => {
    const url = ragUrl.trim();
    if (!url) return;
    setBusy(true);
    setError(null);
    try {
      await api.addRagWebsite(campaignId, url);
      setRagUrl('');
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteRag = async (fileId: number) => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteRagFile(campaignId, fileId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onGenerate = async () => {
    setBusy(true);
    setError(null);
    setStopping(false);
    try {
      setGenStartedAt(Date.now());
      await api.generate(campaignId, enrichWebsites);
      await refreshProgress();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onSaveSegmentsConfig = async (nextUse?: boolean, nextField?: string) => {
    const use = nextUse ?? useSegmentTemplates;
    const field = nextField ?? segmentField;
    setBusy(true);
    setError(null);
    try {
      const segs = await api.updateSegmentsConfig(campaignId, {
        use_segment_templates: use,
        segment_field: use ? field || null : null,
      });
      setUseSegmentTemplates(use);
      setSegmentField(use ? field : '');
      setSegments(segs);
      const c = await api.getCampaign(campaignId);
      setCampaign(c);
      setEstimate(await api.costEstimate(campaignId).catch(() => null));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onGenerateTemplates = async () => {
    setTemplatesBusy(true);
    setError(null);
    try {
      const segs = await api.generateSegmentTemplates(campaignId);
      setSegments(segs);
      if (segs[0]) setOpenSegmentId(segs[0].id);
      setEstimate(await api.costEstimate(campaignId).catch(() => null));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTemplatesBusy(false);
    }
  };

  const onSaveSegmentTemplate = async (
    segmentId: number,
    patch: {
      subject_template?: string;
      body_template?: string;
      status?: 'draft' | 'ready';
    },
  ) => {
    setBusy(true);
    setError(null);
    try {
      const tpl = await api.updateSegmentTemplate(campaignId, segmentId, patch);
      setSegments((prev) =>
        prev.map((s) => (s.id === segmentId ? { ...s, template: tpl } : s)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onStopGenerate = async () => {
    setStopping(true);
    setError(null);
    try {
      await api.stopGenerate(campaignId);
      await refreshProgress();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStopping(false);
    }
  };

  const onSaveDraft = async (draft: Draft, subject: string, body: string) => {
    setError(null);
    try {
      await api.updateDraft(draft.id, { subject, body_text: body });
      await refreshProgress();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const onRegenerate = async (draftId: number) => {
    setBusy(true);
    setError(null);
    try {
      await api.regenerateDraft(draftId, enrichWebsites);
      await refreshProgress();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!campaign) {
    return (
      <>
        <p className="page-sub">
          <Link to="/">← К кампаниям</Link>
        </p>
        {error ? <div className="error-box">{error}</div> : <p className="empty">Загрузка…</p>}
      </>
    );
  }

  const templatesReady =
    !useSegmentTemplates ||
    (segments.length > 0 &&
      segments.every(
        (s) =>
          s.template?.status === 'ready' &&
          (s.template.subject_template || '').trim() &&
          (s.template.body_template || '').trim(),
      ));

  const tabs: { id: typeof tab; label: string; hint?: string }[] = [
    { id: 'settings', label: 'Настройки' },
    { id: 'recipients', label: 'Получатели', hint: String(recipients.length) },
    {
      id: 'segments',
      label: 'Сегменты',
      hint: useSegmentTemplates ? String(segments.length) : undefined,
    },
    { id: 'rag', label: 'RAG', hint: String(ragFiles.length) },
    { id: 'generate', label: 'Генерация' },
    { id: 'drafts', label: 'Письма', hint: drafts.length ? String(drafts.length) : undefined },
    { id: 'export', label: 'Экспорт' },
  ];

  return (
    <div className="campaign-page">
      <p className="page-sub" style={{ marginBottom: '0.5rem' }}>
        <Link to="/">← К кампаниям</Link>
      </p>
      <h1 className="page-title">{campaign.name}</h1>
      <p className="page-sub">
        Статус:{' '}
        <span className={`badge ${campaign.status}`}>{STATUS_RU[campaign.status] ?? campaign.status}</span>
        {campaign.use_web_search ? ' · веб-поиск вкл.' : ' · веб-поиск выкл.'}
        {campaign.llm_model ? ` · ${campaign.llm_model}` : ''}
      </p>

      {error && <div className="error-box">{error}</div>}

      {createWarnings.length > 0 && (
        <div className="error-box" role="alert">
          <strong>Кампания создана, но есть проблемы с RAG:</strong>
          <ul style={{ margin: '0.5rem 0 0', paddingLeft: '1.2rem' }}>
            {createWarnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
          <p className="muted" style={{ margin: '0.75rem 0 0', color: 'inherit' }}>
            Можно загрузить RAG во вкладке «RAG» и затем запустить генерацию.
          </p>
          <button
            type="button"
            className="btn btn-ghost"
            style={{ marginTop: '0.5rem' }}
            onClick={() => setCreateWarnings([])}
          >
            Скрыть
          </button>
        </div>
      )}

      <div className="campaign-workspace">
        <aside className="campaign-workspace__side">
          <CampaignProgressPanel
            campaignStatus={campaign.status}
            status={status}
            drafts={drafts}
            startedAt={genStartedAt}
            filter={progressFilter}
            onFilterChange={setProgressFilter}
            onStop={() => void onStopGenerate()}
            stopping={stopping}
            segmentField={campaign.segment_field}
          />
        </aside>

        <div className="campaign-workspace__main">
          <div className="campaign-tabs" role="tablist" aria-label="Разделы кампании">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                className={`campaign-tabs__btn ${tab === t.id ? 'is-active' : ''}`}
                onClick={() => setTab(t.id)}
              >
                {t.label}
                {t.hint != null && <span className="campaign-tabs__hint">{t.hint}</span>}
              </button>
            ))}
          </div>

          <div className="panel campaign-tab-panel" role="tabpanel">
            {tab === 'settings' && (
              <>
                <h2>Промпт и модель</h2>
                <form className="stack" onSubmit={onSavePrompt}>
                  <label className="field">
                    Модель (цена prompt / completion)
                    <select value={modelId} onChange={(e) => setModelId(e.target.value)}>
                      {models.map((m) => (
                        <option key={m.id} value={m.id}>
                          {modelOptionLabel(m)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    Базовый промпт кампании
                    <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} required />
                  </label>
                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={useWebSearch}
                      onChange={(e) => setUseWebSearch(e.target.checked)}
                    />
                    Использовать веб-поиск OpenRouter (openrouter:web_search)
                  </label>
                  <div className="row">
                    <button className="btn btn-secondary" type="submit" disabled={busy}>
                      Сохранить настройки
                    </button>
                  </div>
                </form>
              </>
            )}

            {tab === 'recipients' && (
              <>
                <h2>Получатели (Excel)</h2>
                <p className="muted" style={{ marginTop: 0 }}>
                  Обязательна колонка <code>email</code>. Все колонки после email попадают в промпт как
                  данные строки.
                </p>
                <div className="row" style={{ marginBottom: '1rem' }}>
                  <input
                    type="file"
                    accept=".csv,.xlsx,.xls"
                    disabled={busy}
                    onChange={(e) => void onImport(e.target.files?.[0] ?? null)}
                  />
                </div>
                {importInfo && <p className="muted">{importInfo}</p>}
                {recipients.length === 0 ? (
                  <p className="empty">Получатели ещё не загружены.</p>
                ) : (
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th>Email</th>
                          <th>Компания</th>
                          <th>Поля Excel</th>
                        </tr>
                      </thead>
                      <tbody>
                        {recipients.map((r) => (
                          <tr key={r.id}>
                            <td>{r.email}</td>
                            <td>{r.company_name}</td>
                            <td className="muted">
                              {r.extra_fields
                                ? Object.keys(r.extra_fields).slice(0, 6).join(', ') +
                                  (Object.keys(r.extra_fields).length > 6 ? '…' : '')
                                : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}

            {tab === 'segments' && (
              <>
                <h2>Сегментные шаблоны</h2>
                <p className="muted" style={{ marginTop: 0 }}>
                  Опционально: один шаблон на значение столбца Excel, зоны{' '}
                  <code>{'{{name}}'}</code> заполняются при генерации писем. Сейчас вариант A; позже —
                  варианты B для A/B-тестов.
                </p>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={useSegmentTemplates}
                    disabled={busy || recipients.length === 0}
                    onChange={(e) => {
                      const next = e.target.checked;
                      setUseSegmentTemplates(next);
                      if (!next) {
                        void onSaveSegmentsConfig(false, '');
                      } else if (segmentField) {
                        void onSaveSegmentsConfig(true, segmentField);
                      }
                    }}
                  />
                  Генерировать через сегментные шаблоны
                </label>
                {recipients.length === 0 ? (
                  <p className="empty">Сначала загрузите Excel на вкладке «Получатели».</p>
                ) : (
                  <>
                    <div className="row" style={{ marginTop: '0.75rem', flexWrap: 'wrap' }}>
                      <label className="field" style={{ flex: 1, minWidth: 220, margin: 0 }}>
                        Поле сегментации
                        <select
                          value={segmentField}
                          disabled={busy || segmentFields.length === 0 || !useSegmentTemplates}
                          onChange={(e) => {
                            const v = e.target.value;
                            setSegmentField(v);
                            if (useSegmentTemplates && v) void onSaveSegmentsConfig(true, v);
                          }}
                        >
                          <option value="">
                            {segmentFields.length === 0
                              ? 'Нет распознанных столбцов'
                              : 'Выберите столбец…'}
                          </option>
                          {segmentFields.map((f) => (
                            <option key={f.key} value={f.key}>
                              {f.label} ({f.non_empty_count})
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        disabled={
                          busy || !useSegmentTemplates || !segmentField || segmentFields.length === 0
                        }
                        onClick={() => void onSaveSegmentsConfig(true, segmentField)}
                      >
                        Пересобрать сегменты
                      </button>
                      <button
                        className="btn"
                        type="button"
                        disabled={
                          templatesBusy ||
                          busy ||
                          !useSegmentTemplates ||
                          segments.length === 0
                        }
                        onClick={() => void onGenerateTemplates()}
                      >
                        {templatesBusy ? 'Генерация шаблонов…' : 'Сгенерировать шаблоны'}
                      </button>
                    </div>
                    {segmentFields.length === 0 && (
                      <p className="muted">Сначала загрузите Excel — список столбцов пуст.</p>
                    )}
                    {useSegmentTemplates && segments.length > 0 && (
                      <div className="table-wrap" style={{ marginTop: '1rem' }}>
                        <table className="data">
                          <thead>
                            <tr>
                              <th>Значение</th>
                              <th>N</th>
                              <th>Шаблон</th>
                            </tr>
                          </thead>
                          <tbody>
                            {segments.map((s) => (
                              <tr key={s.id}>
                                <td>{s.label}</td>
                                <td>{s.recipient_count}</td>
                                <td>
                                  <span
                                    className={`badge ${s.template?.status === 'ready' ? 'ready' : 'draft'}`}
                                  >
                                    {s.template?.status === 'ready' ? 'ready' : 'draft'}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {useSegmentTemplates &&
                      segments.map((s) => {
                        const tpl = s.template;
                        const open = openSegmentId === s.id;
                        return (
                          <div key={s.id} className="segment-card">
                            <button
                              type="button"
                              className="segment-card__head"
                              onClick={() => setOpenSegmentId(open ? null : s.id)}
                            >
                              <strong>{s.label}</strong>
                              <span className="muted">
                                {s.recipient_count} · вариант A ·{' '}
                                {tpl?.status === 'ready' ? 'ready' : 'draft'}
                              </span>
                            </button>
                            {open && tpl && (
                              <div className="stack segment-card__body">
                                <label className="field">
                                  Тема (шаблон)
                                  <input
                                    value={tpl.subject_template}
                                    disabled={busy}
                                    onChange={(e) =>
                                      setSegments((prev) =>
                                        prev.map((x) =>
                                          x.id === s.id && x.template
                                            ? {
                                                ...x,
                                                template: {
                                                  ...x.template,
                                                  subject_template: e.target.value,
                                                  status: 'draft',
                                                },
                                              }
                                            : x,
                                        ),
                                      )
                                    }
                                  />
                                </label>
                                <label className="field">
                                  Тело (шаблон)
                                  <textarea
                                    rows={8}
                                    value={tpl.body_template}
                                    disabled={busy}
                                    onChange={(e) =>
                                      setSegments((prev) =>
                                        prev.map((x) =>
                                          x.id === s.id && x.template
                                            ? {
                                                ...x,
                                                template: {
                                                  ...x.template,
                                                  body_template: e.target.value,
                                                  status: 'draft',
                                                },
                                              }
                                            : x,
                                        ),
                                      )
                                    }
                                  />
                                </label>
                                {(tpl.subject_template || tpl.body_template) && (
                                  <div className="zone-preview muted">
                                    <div
                                      dangerouslySetInnerHTML={{
                                        __html: highlightZones(
                                          `${tpl.subject_template}\n\n${tpl.body_template}`,
                                        ),
                                      }}
                                    />
                                  </div>
                                )}
                                {tpl.zones?.length > 0 && (
                                  <ul className="muted" style={{ margin: 0, paddingLeft: '1.1rem' }}>
                                    {tpl.zones.map((z) => (
                                      <li key={z.name}>
                                        <code>{`{{${z.name}}}`}</code>
                                        {z.hint ? ` — ${z.hint}` : ''}
                                      </li>
                                    ))}
                                  </ul>
                                )}
                                <div className="row">
                                  <button
                                    className="btn btn-secondary"
                                    type="button"
                                    disabled={busy}
                                    onClick={() =>
                                      void onSaveSegmentTemplate(s.id, {
                                        subject_template: tpl.subject_template,
                                        body_template: tpl.body_template,
                                        status: 'draft',
                                      })
                                    }
                                  >
                                    Сохранить
                                  </button>
                                  <button
                                    className="btn"
                                    type="button"
                                    disabled={
                                      busy ||
                                      !(tpl.subject_template || '').trim() ||
                                      !(tpl.body_template || '').trim()
                                    }
                                    onClick={() =>
                                      void onSaveSegmentTemplate(s.id, {
                                        subject_template: tpl.subject_template,
                                        body_template: tpl.body_template,
                                        status: 'ready',
                                      })
                                    }
                                  >
                                    Утвердить (ready)
                                  </button>
                                  {tpl.status === 'ready' && (
                                    <button
                                      className="btn btn-ghost"
                                      type="button"
                                      disabled={busy}
                                      onClick={() =>
                                        void onSaveSegmentTemplate(s.id, { status: 'draft' })
                                      }
                                    >
                                      Снять утверждение
                                    </button>
                                  )}
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                  </>
                )}
              </>
            )}

            {tab === 'rag' && (
              <>
                <h2>RAG (файлы и сайты)</h2>
                <p className="muted" style={{ marginTop: 0 }}>
                  PDF, DOCX, PPTX, TXT, MD, CSV, Excel и страницы сайтов — общий digest на всю кампанию.
                </p>
                <div className="row" style={{ marginBottom: '1rem' }}>
                  <input
                    type="file"
                    multiple
                    accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.xls,.pptx"
                    disabled={busy}
                    onChange={(e) => void onUploadRag(e.target.files)}
                  />
                </div>
                <div className="row" style={{ marginBottom: '1rem' }}>
                  <input
                    type="url"
                    placeholder="https://example.com/product"
                    value={ragUrl}
                    disabled={busy}
                    onChange={(e) => setRagUrl(e.target.value)}
                    style={{ flex: 1, minWidth: 220 }}
                  />
                  <button
                    className="btn btn-secondary"
                    type="button"
                    disabled={busy || !ragUrl.trim()}
                    onClick={() => void onAddRagUrl()}
                  >
                    Добавить сайт
                  </button>
                </div>
                {ragFiles.length === 0 ? (
                  <p className="empty">RAG-источники не загружены.</p>
                ) : (
                  <ul className="provider-list">
                    {ragFiles.map((f) => (
                      <li key={f.id} className="row" style={{ justifyContent: 'space-between' }}>
                        <span>
                          <span className="badge" style={{ marginRight: '0.4rem' }}>
                            {f.source_type === 'website' ? 'сайт' : 'файл'}
                          </span>
                          {f.source_type === 'website' && f.source_url ? (
                            <a href={f.source_url} target="_blank" rel="noreferrer">
                              {f.source_url}
                            </a>
                          ) : (
                            f.filename
                          )}{' '}
                          <span className="muted">({f.char_count.toLocaleString('ru-RU')} симв.)</span>
                        </span>
                        <button
                          className="btn btn-ghost"
                          type="button"
                          disabled={busy}
                          onClick={() => void onDeleteRag(f.id)}
                        >
                          Удалить
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}

            {tab === 'generate' && (
              <>
                <h2>Запуск генерации</h2>
                <div className="stack">
                  {useSegmentTemplates && !templatesReady && (
                    <div className="error-box" role="status">
                      Сначала утвердите шаблоны на вкладке «Сегменты»
                    </div>
                  )}
                  {estimate && (
                    <div>
                      <p style={{ margin: '0 0 0.35rem' }}>
                        Оценка: <strong>~${estimate.estimated_cost_usd.toFixed(4)}</strong> · модель{' '}
                        {estimate.model_id} · {estimate.price_label}
                      </p>
                      <p className="muted" style={{ margin: 0 }}>
                        ~{estimate.estimated_prompt_tokens.toLocaleString('ru-RU')} prompt +{' '}
                        {estimate.estimated_completion_tokens.toLocaleString('ru-RU')} completion tok
                        {estimate.estimated_digest_tokens
                          ? ` + ~${estimate.estimated_digest_tokens.toLocaleString('ru-RU')} на RAG-digest`
                          : ''}
                      </p>
                      <ul className="muted" style={{ margin: '0.5rem 0 0', paddingLeft: '1.1rem' }}>
                        {estimate.notes.map((n) => (
                          <li key={n}>{n}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={enrichWebsites}
                      onChange={(e) => setEnrichWebsites(e.target.checked)}
                    />
                    Дополнительно анализировать сайты (дорого, по умолчанию выкл.)
                  </label>
                  <div className="row">
                    <button
                      className="btn"
                      type="button"
                      disabled={
                        busy ||
                        recipients.length === 0 ||
                        campaign.status === 'generating' ||
                        (useSegmentTemplates && !templatesReady)
                      }
                      onClick={() => void onGenerate()}
                    >
                      {campaign.status === 'generating'
                        ? 'Генерация…'
                        : useSegmentTemplates
                          ? 'Сгенерировать письма (зоны)'
                          : 'Сгенерировать письма'}
                    </button>
                    {campaign.status === 'generating' && (
                      <button
                        className="btn btn-ghost"
                        type="button"
                        disabled={stopping}
                        onClick={() => void onStopGenerate()}
                      >
                        {stopping ? 'Остановка…' : 'Остановить'}
                      </button>
                    )}
                    <button
                      className="btn btn-secondary"
                      type="button"
                      disabled={busy}
                      onClick={() => void api.costEstimate(campaignId).then(setEstimate)}
                    >
                      Обновить оценку
                    </button>
                  </div>
                </div>
              </>
            )}

            {tab === 'drafts' && (
              <>
                <h2>Ревью черновиков</h2>
                {drafts.length === 0 ? (
                  <p className="empty">Черновиков пока нет — запустите генерацию.</p>
                ) : (
                  drafts.map((draft) => (
                    <DraftEditor
                      key={draft.id}
                      draft={draft}
                      busy={busy || campaign.status === 'generating'}
                      onSave={onSaveDraft}
                      onRegenerate={onRegenerate}
                    />
                  ))
                )}
              </>
            )}

            {tab === 'export' && (
              <>
                <h2>Экспорт и отправка</h2>
                <div className="row" style={{ marginBottom: '1rem' }}>
                  <a className="btn btn-secondary" href={api.exportUrl(campaignId, 'csv')}>
                    Скачать CSV
                  </a>
                  <a className="btn btn-secondary" href={api.exportUrl(campaignId, 'json')}>
                    Скачать JSON
                  </a>
                  <button className="btn" type="button" disabled title="Интеграции скоро">
                    Отправить
                  </button>
                </div>
                <ul className="provider-list">
                  {providers.map((p) => (
                    <li key={p.type}>
                      <strong>{p.display_name}</strong>
                      <span className="badge" style={{ marginLeft: '0.5rem' }}>
                        скоро
                      </span>
                      <p className="muted" style={{ margin: '0.35rem 0 0' }}>
                        {p.description}
                      </p>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function DraftEditor({
  draft,
  busy,
  onSave,
  onRegenerate,
}: {
  draft: Draft;
  busy: boolean;
  onSave: (draft: Draft, subject: string, body: string) => Promise<void>;
  onRegenerate: (id: number) => Promise<void>;
}) {
  const [subject, setSubject] = useState(draft.subject ?? '');
  const [body, setBody] = useState(draft.body_text ?? '');

  useEffect(() => {
    setSubject(draft.subject ?? '');
    setBody(draft.body_text ?? '');
  }, [draft.subject, draft.body_text, draft.id, draft.updated_at]);

  return (
    <div className="draft-card" id={`draft-edit-${draft.id}`}>
      <div className="draft-meta">
        <strong>{draft.recipient?.company_name ?? 'Компания'}</strong>
        <span>{draft.recipient?.email}</span>
        <span className={`badge ${draft.status}`}>{draft.status}</span>
      </div>
      {draft.error && <div className="error-box">{draft.error}</div>}
      <div className="stack">
        <label className="field">
          Тема
          <input value={subject} onChange={(e) => setSubject(e.target.value)} />
        </label>
        <label className="field">
          Текст
          <textarea value={body} onChange={(e) => setBody(e.target.value)} style={{ minHeight: 160 }} />
        </label>
        <div className="row">
          <button
            className="btn btn-secondary"
            type="button"
            disabled={busy}
            onClick={() => void onSave(draft, subject, body)}
          >
            Сохранить правки
          </button>
          <button
            className="btn btn-ghost"
            type="button"
            disabled={busy || draft.status === 'generating'}
            onClick={() => void onRegenerate(draft.id)}
          >
            Перегенерировать
          </button>
        </div>
      </div>
    </div>
  );
}
