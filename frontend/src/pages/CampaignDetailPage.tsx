import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  api,
  type Campaign,
  type CampaignDashboard,
  type CampaignStatusOut,
  type CostEstimate,
  type Draft,
  type DraftStatus,
  type LLMModel,
  type MetrikaCounter,
  type MetrikaGoal,
  type Provider,
  type RagFile,
  type Recipient,
  type Segment,
  type SegmentField,
  type SendProgress,
  type SendPreview,
  type YandexStatus,
} from '../api';
import {
  CAMPAIGN_TAB_LABELS,
  campaignTabPath,
  isCampaignTab,
  parseCampaignTab,
  type CampaignTab,
} from '../campaignTabs';
import { CampaignProgressPanel } from '../components/CampaignProgressPanel';

function modelOptionLabel(m: LLMModel): string {
  const price = m.price_label ? ` — ${m.price_label}` : '';
  return `${m.name}${price}`;
}

function highlightZones(text: string): string {
  return text.replace(
    /\{\{\s*([^{}]+?)\s*\}\}/g,
    '<mark class="zone-mark">{{$1}}</mark>',
  );
}

const STATUS_RU: Record<string, string> = {
  draft: 'черновик',
  generating: 'генерация',
  ready: 'готово',
};

const SEND_STATUS_RU: Record<string, { label: string; hint: string; tone: string }> = {
  queued: {
    label: 'В очереди',
    hint: 'Письмо ждёт своей очереди. Между отправками пауза ~45 сек.',
    tone: 'pending',
  },
  sending: {
    label: 'Отправляется',
    hint: 'Сейчас уходит через SMTP Яндекса.',
    tone: 'generating',
  },
  sent: {
    label: 'Отправлено',
    hint: 'SMTP принял письмо. Открытие/клик появятся, если трекинг доступен снаружи.',
    tone: 'ready',
  },
  failed: {
    label: 'Ошибка',
    hint: 'Не удалось отправить. Смотрите текст ошибки в таблице.',
    tone: 'error',
  },
};

export function CampaignDetailPage() {
  const { id, tab: tabParam } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const campaignId = Number(id);
  const tab = parseCampaignTab(tabParam);

  const goTab = useCallback(
    (next: CampaignTab) => {
      navigate(campaignTabPath(campaignId, next), { replace: true });
    },
    [campaignId, navigate],
  );

  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [recipients, setRecipients] = useState<Recipient[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [status, setStatus] = useState<CampaignStatusOut | null>(null);
  const [, setProviders] = useState<Provider[]>([]);
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
  const [yandex, setYandex] = useState<YandexStatus | null>(null);
  const [oauthCode, setOauthCode] = useState('');
  const [bannerUrl, setBannerUrl] = useState(
    'https://comquaagent.copp-hightech.ru/emails/cold-audit.html',
  );
  const [bannerReady, setBannerReady] = useState(false);
  const [counters, setCounters] = useState<MetrikaCounter[]>([]);
  const [goals, setGoals] = useState<MetrikaGoal[]>([]);
  const [counterId, setCounterId] = useState<number | ''>('');
  const [goalId, setGoalId] = useState<number | ''>('');
  const [sendProg, setSendProg] = useState<SendProgress | null>(null);
  const [dashboard, setDashboard] = useState<CampaignDashboard | null>(null);
  const [dashFilter, setDashFilter] = useState<'all' | 'sent' | 'queued' | 'failed' | 'opened' | 'clicked'>(
    'all',
  );
  const [preview, setPreview] = useState<SendPreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
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
      api.listModels().catch(() => [] as LLMModel[]),
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
    setCounterId(c.metrika_counter_id ?? '');
    setGoalId(c.metrika_goal_id ?? '');
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
    if (tabParam && !isCampaignTab(tabParam)) {
      navigate(campaignTabPath(campaignId, 'settings'), { replace: true });
    }
  }, [campaignId, tabParam, navigate]);

  useEffect(() => {
    if (!Number.isFinite(campaignId)) return;
    void refresh().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [campaignId, refresh]);

  useEffect(() => {
    const generating = campaign?.status === 'generating';
    if (generating && !wasGenerating.current) {
      setGenStartedAt((prev) => prev ?? Date.now());
      setProgressFilter('all');
      goTab('drafts');
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
  }, [campaign?.status, refreshProgress, goTab]);

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

  const refreshIntegrations = async () => {
    const st = await api.yandexStatus();
    setYandex(st);
    if (st.connected) {
      try {
        const cs = await api.metrikaCounters();
        setCounters(cs);
      } catch {
        setCounters([]);
      }
    }
    const ban = await api.getBanner(campaignId).catch(() => null);
    if (ban) {
      setBannerReady(ban.has_content);
      if (ban.source_url) setBannerUrl(ban.source_url);
    }
  };

  const onYandexAuthStart = async () => {
    setBusy(true);
    setError(null);
    try {
      const { authorize_url } = await api.yandexOAuthStart();
      window.open(authorize_url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onYandexExchange = async () => {
    if (!oauthCode.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const st = await api.yandexOAuthExchange(oauthCode.trim());
      setYandex(st);
      setOauthCode('');
      await refreshIntegrations();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onSaveBanner = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.putBanner(campaignId, { source_url: bannerUrl.trim() });
      setBannerReady(res.has_content);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onSaveMetrika = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.updateMetrikaConfig(campaignId, {
        metrika_counter_id: counterId === '' ? null : Number(counterId),
        metrika_goal_id: goalId === '' ? null : Number(goalId),
        landing_url: 'https://comquaagent.copp-hightech.ru/',
      });
      if (counterId !== '') {
        setGoals(await api.metrikaGoals(Number(counterId)));
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onSendBatch = async (limit?: number) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.sendCampaign(campaignId, {
        limit,
        track: true,
        throttle_seconds: 45,
      });
      setSendProg(await api.sendProgress(campaignId));
      if (res.message) setImportInfo(res.message);
      else setImportInfo(`В очереди: ${res.queued ?? 0}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onConfirmSent = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.sendConfirm(campaignId);
      const base = `IMAP: проверено ${res.checked}, подтверждено ${res.confirmed}`;
      setImportInfo(res.note ? `${base}. ${res.note}` : base);
      setSendProg(await api.sendProgress(campaignId));
      setDashboard(await api.campaignDashboard(campaignId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onLoadDashboard = async () => {
    setBusy(true);
    setError(null);
    try {
      setDashboard(await api.campaignDashboard(campaignId));
      setSendProg(await api.sendProgress(campaignId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onOpenSendPreview = async (sendId: number) => {
    setPreviewBusy(true);
    setError(null);
    try {
      setPreview(await api.sendPreview(campaignId, sendId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPreviewBusy(false);
    }
  };

  useEffect(() => {
    if (!preview && !previewBusy) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !previewBusy) setPreview(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [preview, previewBusy]);

  useEffect(() => {
    if (tab !== 'integrations' && tab !== 'send' && tab !== 'dashboard') return;
    void refreshIntegrations().catch(() => undefined);
    if (tab === 'dashboard' || tab === 'send') {
      void api.sendProgress(campaignId).then(setSendProg).catch(() => undefined);
    }
    if (tab === 'dashboard') {
      void api.campaignDashboard(campaignId).then(setDashboard).catch(() => undefined);
    }
  }, [tab, campaignId]);

  useEffect(() => {
    if (!sendProg?.running) return;
    const t = window.setInterval(() => {
      void api.sendProgress(campaignId).then(setSendProg).catch(() => undefined);
      if (tab === 'dashboard') {
        void api.campaignDashboard(campaignId).then(setDashboard).catch(() => undefined);
      }
    }, 3000);
    return () => window.clearInterval(t);
  }, [sendProg?.running, campaignId, tab]);


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

  const dashStats = useMemo(() => {
    const sends = dashboard?.sends ?? [];
    const progress = dashboard?.progress ?? sendProg;
    const counts = progress?.counts ?? {};
    const opens = sends.reduce((a, s) => a + (s.opens || 0), 0);
    const clicks = sends.reduce((a, s) => a + (s.clicks || 0), 0);
    const openedPeople = sends.filter((s) => (s.opens || 0) > 0 || s.unique_open).length;
    const clickedPeople = sends.filter((s) => (s.clicks || 0) > 0).length;
    const metrikaRows = dashboard?.metrika?.rows ?? [];
    const metrikaVisits = metrikaRows.reduce((a, r) => a + Number(r.visits ?? 0), 0);
    const metrikaGoals = metrikaRows.reduce((a, r) => {
      const g = r.goal_reaches;
      return a + (g == null || g === '' ? 0 : Number(g));
    }, 0);
    return {
      total: progress?.total ?? sends.length,
      sent: counts.sent ?? 0,
      queued: (counts.queued ?? 0) + (counts.sending ?? 0),
      failed: counts.failed ?? 0,
      confirmed: progress?.confirmed_in_sent ?? 0,
      opens,
      clicks,
      openedPeople,
      clickedPeople,
      metrikaVisits,
      metrikaGoals,
      running: !!progress?.running,
      hasMetrikaError: !!dashboard?.metrika?.error,
      metrikaConfigured: !!(dashboard?.metrika_counter_id ?? campaign?.metrika_counter_id),
    };
  }, [dashboard, sendProg, campaign?.metrika_counter_id]);

  const filteredSends = useMemo(() => {
    const sends = dashboard?.sends ?? [];
    switch (dashFilter) {
      case 'sent':
        return sends.filter((s) => s.status === 'sent');
      case 'queued':
        return sends.filter((s) => s.status === 'queued' || s.status === 'sending');
      case 'failed':
        return sends.filter((s) => s.status === 'failed');
      case 'opened':
        return sends.filter((s) => (s.opens || 0) > 0 || s.unique_open);
      case 'clicked':
        return sends.filter((s) => (s.clicks || 0) > 0);
      default:
        return sends;
    }
  }, [dashboard?.sends, dashFilter]);

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

  const tabs: { id: CampaignTab; label: string; hint?: string }[] = [
    { id: 'settings', label: CAMPAIGN_TAB_LABELS.settings },
    { id: 'recipients', label: CAMPAIGN_TAB_LABELS.recipients, hint: String(recipients.length) },
    {
      id: 'segments',
      label: CAMPAIGN_TAB_LABELS.segments,
      hint: useSegmentTemplates ? String(segments.length) : undefined,
    },
    { id: 'rag', label: CAMPAIGN_TAB_LABELS.rag, hint: String(ragFiles.length) },
    { id: 'generate', label: CAMPAIGN_TAB_LABELS.generate },
    {
      id: 'drafts',
      label: CAMPAIGN_TAB_LABELS.drafts,
      hint: drafts.length ? String(drafts.length) : undefined,
    },
    { id: 'integrations', label: CAMPAIGN_TAB_LABELS.integrations },
    { id: 'send', label: CAMPAIGN_TAB_LABELS.send },
    { id: 'dashboard', label: CAMPAIGN_TAB_LABELS.dashboard },
    { id: 'export', label: CAMPAIGN_TAB_LABELS.export },
  ];

  const dashHeadline = (() => {
    if (dashStats.running) {
      return {
        title: 'Идёт рассылка',
        hint: `Отправлено ${dashStats.sent} из ${dashStats.total}. Между письмами пауза ~45 сек — это нормально.`,
        tone: 'warn' as const,
      };
    }
    if (dashStats.total === 0) {
      return {
        title: 'Рассылка ещё не запускалась',
        hint: 'Соберите баннер и Яндекс OAuth во вкладке «Интеграции», затем отправьте тест или всю очередь во вкладке «Рассылка».',
        tone: 'muted' as const,
      };
    }
    if (dashStats.queued > 0) {
      return {
        title: 'Есть письма в очереди',
        hint: 'Отправка сейчас не активна. Запустите её снова во вкладке «Рассылка».',
        tone: 'warn' as const,
      };
    }
    if (dashStats.failed > 0 && dashStats.sent === 0) {
      return {
        title: 'Отправки не прошли',
        hint: 'Проверьте ошибки в таблице и подключение Яндекса.',
        tone: 'danger' as const,
      };
    }
    return {
      title: 'Рассылка завершена',
      hint: `Ушло ${dashStats.sent} писем${dashStats.failed ? `, ошибок: ${dashStats.failed}` : ''}. Ниже — открытия, клики и данные Метрики.`,
      tone: 'ok' as const,
    };
  })();

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
      {importInfo && <p className="muted">{importInfo}</p>}

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
                onClick={() => goTab(t.id)}
              >
                {t.label}
                {t.hint != null && <span className="campaign-tabs__hint">{t.hint}</span>}
              </button>
            ))}
          </div>

          <p className="muted campaign-tab-link">
            Ссылка на вкладку:{' '}
            <code>{campaignTabPath(campaignId, tab)}</code>
          </p>

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
                <h2>Экспорт</h2>
                <div className="row" style={{ marginBottom: '1rem' }}>
                  <a className="btn btn-secondary" href={api.exportUrl(campaignId, 'csv')}>
                    Скачать CSV
                  </a>
                  <a className="btn btn-secondary" href={api.exportUrl(campaignId, 'json')}>
                    Скачать JSON
                  </a>
                </div>
                <p className="muted">
                  Отправка через Яндекс.Почту — вкладки «Интеграции» и «Рассылка».
                </p>
              </>
            )}

            {tab === 'integrations' && (
              <>
                <h2>Яндекс: Почта + Метрика</h2>
                <p className="muted" style={{ marginTop: 0 }}>
                  Права OAuth: <code>mail:smtp</code>, <code>mail:imap_ro</code>,{' '}
                  <code>metrika:read</code>. Redirect:{' '}
                  <code>https://oauth.yandex.ru/verification_code</code>
                </p>
                <div className="stack">
                  <p>
                    Статус:{' '}
                    {yandex?.connected ? (
                      <span className="badge ready">подключено · {yandex.email}</span>
                    ) : (
                      <span className="badge draft">не подключено</span>
                    )}
                  </p>
                  <div className="row">
                    <button
                      className="btn"
                      type="button"
                      disabled={busy}
                      onClick={() => void onYandexAuthStart()}
                    >
                      1. Открыть авторизацию Яндекс
                    </button>
                    {yandex?.connected && (
                      <button
                        className="btn btn-ghost"
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void api.yandexDisconnect().then(() => refreshIntegrations())
                        }
                      >
                        Отключить
                      </button>
                    )}
                  </div>
                  <label className="field">
                    2. Код со страницы verification_code
                    <input
                      value={oauthCode}
                      onChange={(e) => setOauthCode(e.target.value)}
                      placeholder="Вставьте код"
                      disabled={busy}
                    />
                  </label>
                  <button
                    className="btn btn-secondary"
                    type="button"
                    disabled={busy || !oauthCode.trim()}
                    onClick={() => void onYandexExchange()}
                  >
                    Обменять код на токен
                  </button>

                  {yandex?.connected && (
                    <>
                      <h3>Метрика</h3>
                      <label className="field">
                        Счётчик
                        <select
                          value={counterId}
                          onChange={(e) => {
                            const v = e.target.value ? Number(e.target.value) : '';
                            setCounterId(v);
                            setGoalId('');
                            if (v !== '') {
                              void api.metrikaGoals(Number(v)).then(setGoals).catch(() => setGoals([]));
                            } else setGoals([]);
                          }}
                        >
                          <option value="">Выберите счётчик…</option>
                          {counters.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name} ({c.id}){c.site ? ` — ${c.site}` : ''}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        Цель формы
                        <select
                          value={goalId}
                          onChange={(e) => setGoalId(e.target.value ? Number(e.target.value) : '')}
                          disabled={counterId === ''}
                        >
                          <option value="">Без цели / позже</option>
                          {goals.map((g) => (
                            <option key={g.id} value={g.id}>
                              {g.name} ({g.id})
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        disabled={busy}
                        onClick={() => void onSaveMetrika()}
                      >
                        Сохранить Метрику для кампании
                      </button>
                    </>
                  )}
                </div>
              </>
            )}

            {tab === 'send' && (
              <>
                <h2>Рассылка</h2>
                <p className="muted" style={{ marginTop: 0 }}>
                  Баннер + UTM на лендинг + трекинг открытий/кликов. Пауза ~45 с между письмами.
                </p>
                {!yandex?.connected && (
                  <div className="error-box">Сначала подключите Яндекс на вкладке «Интеграции»</div>
                )}
                <div className="stack">
                  <label className="field">
                    URL баннера
                    <input
                      value={bannerUrl}
                      onChange={(e) => setBannerUrl(e.target.value)}
                      disabled={busy}
                    />
                  </label>
                  <div className="row">
                    <button
                      className="btn btn-secondary"
                      type="button"
                      disabled={busy || !bannerUrl.trim()}
                      onClick={() => void onSaveBanner()}
                    >
                      Загрузить баннер
                    </button>
                    {bannerReady && <span className="badge ready">баннер готов</span>}
                  </div>
                  {sendProg && (
                    <p className="muted">
                      Отправки: всего {sendProg.total} · sent {sendProg.counts.sent ?? 0} · failed{' '}
                      {sendProg.counts.failed ?? 0} · queued {sendProg.counts.queued ?? 0}
                      {sendProg.running ? ' · идёт…' : ''} · IMAP confirmed{' '}
                      {sendProg.confirmed_in_sent}
                    </p>
                  )}
                  <p className="muted" style={{ marginTop: 0 }}>
                    Яндекс не кладёт SMTP-письма в Sent сам — после отправки копия пишется через IMAP
                    APPEND. Старые тестовые письма (до этого) в Sent не появятся.
                  </p>
                  <div className="row" style={{ flexWrap: 'wrap' }}>
                    <button
                      className="btn"
                      type="button"
                      disabled={busy || !yandex?.connected || !bannerReady || !!sendProg?.running}
                      onClick={() => void onSendBatch(10)}
                    >
                      Отправить тест (10)
                    </button>
                    <button
                      className="btn btn-secondary"
                      type="button"
                      disabled={busy || !yandex?.connected || !!sendProg?.running}
                      onClick={() => void onConfirmSent()}
                    >
                      {busy ? 'Проверка IMAP…' : 'Проверить доставку (IMAP Sent)'}
                    </button>
                    <button
                      className="btn"
                      type="button"
                      disabled={busy || !yandex?.connected || !bannerReady || !!sendProg?.running}
                      onClick={() => void onSendBatch(undefined)}
                    >
                      Отправить остальные
                    </button>
                  </div>
                </div>
              </>
            )}

            {tab === 'dashboard' && (
              <>
                <div className="dash-head">
                  <div>
                    <h2 style={{ margin: 0 }}>Дашборд рассылки</h2>
                    <p className="muted" style={{ margin: '0.35rem 0 0' }}>
                      Сводка по отправкам, открытиям, кликам и Метрике. Обновляется автоматически, пока
                      идёт рассылка.
                    </p>
                  </div>
                  <div className="row" style={{ flexWrap: 'wrap', gap: '0.5rem' }}>
                    <button
                      className="btn btn-secondary"
                      type="button"
                      disabled={busy}
                      onClick={() => void onLoadDashboard()}
                    >
                      Обновить
                    </button>
                    <Link className="btn btn-ghost" to={campaignTabPath(campaignId, 'send')}>
                      К рассылке
                    </Link>
                    <Link className="btn btn-ghost" to={campaignTabPath(campaignId, 'integrations')}>
                      К интеграциям
                    </Link>
                  </div>
                </div>

                <div className={`dash-banner dash-banner--${dashHeadline.tone}`} role="status">
                  <strong>{dashHeadline.title}</strong>
                  <p>{dashHeadline.hint}</p>
                </div>

                <div className="dash-kpis" aria-label="Ключевые показатели">
                  <div className="dash-kpi">
                    <strong>{dashStats.sent}</strong>
                    <span>Отправлено</span>
                    <small>SMTP принял письмо</small>
                  </div>
                  <div className="dash-kpi">
                    <strong>{dashStats.queued}</strong>
                    <span>В очереди</span>
                    <small>Ещё не ушли</small>
                  </div>
                  <div className={`dash-kpi ${dashStats.failed ? 'is-danger' : ''}`}>
                    <strong>{dashStats.failed}</strong>
                    <span>Ошибки</span>
                    <small>Не удалось отправить</small>
                  </div>
                  <div className="dash-kpi is-ok">
                    <strong>{dashStats.openedPeople}</strong>
                    <span>Открыли</span>
                    <small>{dashStats.opens} срабатываний пикселя</small>
                  </div>
                  <div className="dash-kpi is-ok">
                    <strong>{dashStats.clickedPeople}</strong>
                    <span>Кликнули</span>
                    <small>{dashStats.clicks} переходов по ссылкам</small>
                  </div>
                  <div className="dash-kpi">
                    <strong>{dashStats.confirmed}</strong>
                    <span>В Sent (IMAP)</span>
                    <small>Копия найдена в «Отправленных»</small>
                  </div>
                  <div className="dash-kpi">
                    <strong>{dashStats.metrikaVisits}</strong>
                    <span>Визиты Метрики</span>
                    <small>По UTM из писем</small>
                  </div>
                  <div className="dash-kpi">
                    <strong>
                      {dashStats.metrikaConfigured ? dashStats.metrikaGoals : '—'}
                    </strong>
                    <span>Цели Метрики</span>
                    <small>
                      {dashStats.metrikaConfigured
                        ? 'Достижения выбранной цели'
                        : 'Счётчик не выбран'}
                    </small>
                  </div>
                </div>

                <div className="dash-hints">
                  <h3>Как читать статусы</h3>
                  <ul>
                    {Object.entries(SEND_STATUS_RU).map(([key, meta]) => (
                      <li key={key}>
                        <span className={`badge ${meta.tone}`}>{meta.label}</span>
                        <span>{meta.hint}</span>
                      </li>
                    ))}
                    <li>
                      <span className="badge ready">Открытия / клики</span>
                      <span>
                        Считаются только если письмо открыли с картинками и ссылки ведут на публичный
                        HTTPS-трекинг. Локальный адрес трекинга открытия не пишет.
                      </span>
                    </li>
                    <li>
                      <span className="badge pending">Метрика</span>
                      <span>
                        Визиты и цели появляются после перехода на лендинг с UTM. Задержка у Яндекса —
                        обычно от нескольких минут до часов.
                      </span>
                    </li>
                  </ul>
                </div>

                {dashboard?.metrika?.error && (
                  <div className="error-box">
                    Метрика недоступна: {dashboard.metrika.error}. Проверьте OAuth и счётчик во вкладке
                    «Интеграции».
                  </div>
                )}
                {dashboard?.metrika && !dashboard.metrika.error && (
                  <p className="muted">
                    Период Метрики: {dashboard.metrika.date1} — {dashboard.metrika.date2}
                    {dashboard.landing_url ? ` · лендинг ${dashboard.landing_url}` : ''}
                  </p>
                )}

                <div className="dash-filters" role="group" aria-label="Фильтр отправок">
                  {(
                    [
                      ['all', 'Все'],
                      ['sent', 'Отправленные'],
                      ['queued', 'Очередь'],
                      ['failed', 'Ошибки'],
                      ['opened', 'Открытые'],
                      ['clicked', 'С кликом'],
                    ] as const
                  ).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      className={`btn btn-ghost dash-filter ${dashFilter === id ? 'is-active' : ''}`}
                      onClick={() => setDashFilter(id)}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                <p className="muted" style={{ marginBottom: '0.5rem' }}>
                  Нажмите на строку, чтобы открыть письмо.
                </p>
                <div className="table-wrap">
                  <table className="data dash-sends-table">
                    <thead>
                      <tr>
                        <th>Компания</th>
                        <th>Кому</th>
                        <th>Статус</th>
                        <th>Открытия</th>
                        <th>Клики</th>
                        <th>Sent IMAP</th>
                        <th>Комментарий</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredSends.length === 0 ? (
                        <tr>
                          <td colSpan={7} className="muted">
                            {dashStats.total === 0
                              ? 'Пока нет отправок — начните во вкладке «Рассылка».'
                              : 'Нет строк по выбранному фильтру.'}
                          </td>
                        </tr>
                      ) : (
                        filteredSends.map((s) => {
                          const meta = SEND_STATUS_RU[s.status] ?? {
                            label: s.status,
                            hint: '',
                            tone: 'draft',
                          };
                          const comment =
                            s.error ||
                            (s.confirmed_in_sent
                              ? 'Копия есть в Sent'
                              : s.status === 'sent'
                                ? 'Отправлено; IMAP-подтверждение опционально'
                                : meta.hint);
                          return (
                            <tr
                              key={s.send_id}
                              className="dash-send-row"
                              tabIndex={0}
                              title={s.subject || 'Открыть письмо'}
                              onClick={() => void onOpenSendPreview(s.send_id)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  void onOpenSendPreview(s.send_id);
                                }
                              }}
                            >
                              <td>
                                <strong>{s.company_name || '—'}</strong>
                                {s.subject ? (
                                  <div className="muted dash-send-subject">{s.subject}</div>
                                ) : null}
                              </td>
                              <td>{s.to_email}</td>
                              <td>
                                <span className={`badge ${meta.tone}`} title={meta.hint}>
                                  {meta.label}
                                </span>
                              </td>
                              <td>{s.opens || '—'}</td>
                              <td>{s.clicks || '—'}</td>
                              <td>{s.confirmed_in_sent ? 'да' : '—'}</td>
                              <td className="muted">{comment}</td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>

                {(preview || previewBusy) && (
                  <div
                    className="dash-modal-backdrop"
                    role="presentation"
                    onClick={() => !previewBusy && setPreview(null)}
                  >
                    <div
                      className="dash-modal"
                      role="dialog"
                      aria-modal="true"
                      aria-label="Просмотр письма"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="dash-modal__head">
                        <div>
                          <h3 style={{ margin: 0 }}>
                            {previewBusy
                              ? 'Загрузка…'
                              : preview?.company_name || preview?.to_email || 'Письмо'}
                          </h3>
                          {!previewBusy && preview && (
                            <p className="muted" style={{ margin: '0.3rem 0 0' }}>
                              {preview.to_email}
                              {preview.subject ? ` · ${preview.subject}` : ''}
                            </p>
                          )}
                        </div>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={previewBusy}
                          onClick={() => setPreview(null)}
                        >
                          Закрыть
                        </button>
                      </div>
                      <div className="dash-modal__body">
                        {previewBusy && <p className="muted">Загружаем HTML письма…</p>}
                        {!previewBusy && preview?.html && (
                          <iframe
                            className="dash-mail-frame"
                            title="Превью письма"
                            sandbox="allow-popups allow-popups-to-escape-sandbox"
                            srcDoc={preview.html}
                          />
                        )}
                        {!previewBusy && preview && !preview.html && (
                          <pre className="dash-mail-plain">{preview.body_text || 'Нет содержимого письма'}</pre>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {dashboard?.metrika?.rows && dashboard.metrika.rows.length > 0 && (
                  <>
                    <h3>Метрика: откуда пришли с писем</h3>
                    <p className="muted" style={{ marginTop: 0 }}>
                      Строки по UTM-меткам из ссылок в письмах (campaign / content).
                    </p>
                    <div className="table-wrap">
                      <table className="data">
                        <thead>
                          <tr>
                            <th>Кампания (utm)</th>
                            <th>Контент</th>
                            <th>Визиты</th>
                            <th>Цели</th>
                          </tr>
                        </thead>
                        <tbody>
                          {dashboard.metrika.rows.slice(0, 50).map((r, i) => (
                            <tr key={i}>
                              <td>{String(r.utm_campaign ?? '—')}</td>
                              <td>{String(r.utm_content ?? '—')}</td>
                              <td>{String(r.visits ?? 0)}</td>
                              <td>{String(r.goal_reaches ?? '—')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
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
