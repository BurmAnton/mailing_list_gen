import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, type Campaign, type LLMModel } from '../api';

function modelOptionLabel(m: LLMModel): string {
  const price = m.price_label ? ` — ${m.price_label}` : '';
  return `${m.name}${price}`;
}

export function CampaignListPage() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [models, setModels] = useState<LLMModel[]>([]);
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [modelId, setModelId] = useState('');
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [ragFiles, setRagFiles] = useState<FileList | null>(null);
  const [ragUrls, setRagUrls] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [deleting, setDeleting] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, m] = await Promise.all([api.listCampaigns(), api.listModels()]);
      setCampaigns(c);
      setSelected(new Set());
      setModels(m);
      if (!modelId && m.length) {
        setModelId(m[0].id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // #region agent log
    const payload = {
      sessionId: 'bd0245',
      location: 'CampaignListPage.tsx:mount',
      message: 'page_mount',
      data: { href: typeof window !== 'undefined' ? window.location.href : '' },
      timestamp: Date.now(),
      hypothesisId: 'G',
      runId: 'post-fix',
    };
    fetch('http://127.0.0.1:7869/ingest/f126a795-6571-4f70-980d-f352154f7ca3', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': 'bd0245' },
      body: JSON.stringify(payload),
    }).catch(() => {});
    fetch('http://localhost:8000/api/_debug/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).catch(() => {});
    // #endregion
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setCreating(true);
    // #region agent log
    const _dbg = (message: string, data: Record<string, unknown>, hypothesisId = 'A') => {
      const payload = { sessionId: 'bd0245', location: 'CampaignListPage.tsx:onCreate', message, data, timestamp: Date.now(), hypothesisId, runId: 'post-fix' };
      fetch('http://127.0.0.1:7869/ingest/f126a795-6571-4f70-980d-f352154f7ca3',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'bd0245'},body:JSON.stringify(payload)}).catch(()=>{});
      fetch('http://localhost:8000/api/_debug/log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).catch(()=>{});
    };
    _dbg('create_start', {hasExcel:!!excelFile,excelName:excelFile?.name,ragCount:ragFiles?.length??0,ragNames:ragFiles?Array.from(ragFiles).map(f=>f.name):[],ragUrlsLen:ragUrls.trim().length,modelId});
    // #endregion
    let createdId: number | null = null;
    const warnings: string[] = [];
    try {
      const campaign = await api.createCampaign({
        name,
        base_prompt: prompt,
        llm_provider: 'openrouter',
        llm_model: modelId || null,
        use_web_search: useWebSearch,
      });
      createdId = campaign.id;
      // #region agent log
      _dbg('campaign_created', {id:campaign.id});
      // #endregion
      if (excelFile) {
        await api.importRecipients(campaign.id, excelFile);
      }
      if (ragFiles && ragFiles.length > 0) {
        try {
          await api.uploadRagFiles(campaign.id, Array.from(ragFiles));
          // #region agent log
          _dbg('rag_upload_ok', {campaignId:campaign.id}, 'C');
          // #endregion
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          // #region agent log
          _dbg('rag_upload_fail_soft', {campaignId:campaign.id,error:msg}, 'A');
          // #endregion
          warnings.push(`RAG-файлы не загрузились: ${msg}`);
        }
      }
      const urls = ragUrls
        .split(/[\n,]+/)
        .map((u) => u.trim())
        .filter(Boolean);
      for (const url of urls) {
        try {
          await api.addRagWebsite(campaign.id, url);
        } catch (err) {
          warnings.push(
            `Сайт RAG не загрузился (${url}): ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
      setName('');
      setPrompt('');
      setExcelFile(null);
      setRagFiles(null);
      setRagUrls('');
      setUseWebSearch(false);
      // #region agent log
      _dbg('navigate_success', {id:campaign.id,warnings}, 'E');
      // #endregion
      navigate(`/campaigns/${campaign.id}`, {
        state: warnings.length ? { createWarnings: warnings } : undefined,
      });
    } catch (err) {
      // #region agent log
      _dbg('create_catch_set_error', {error:err instanceof Error?err.message:String(err),createdId}, 'A');
      // #endregion
      const msg = err instanceof Error ? err.message : String(err);
      setError(
        createdId
          ? `Кампания #${createdId} создана, но дальше ошибка: ${msg}. Откройте её в списке.`
          : msg,
      );
      await load();
    } finally {
      setCreating(false);
    }
  };

  const toggleOne = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === campaigns.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(campaigns.map((c) => c.id)));
    }
  };

  const onDeleteSelected = async () => {
    if (selected.size === 0) return;
    const n = selected.size;
    if (!window.confirm(`Удалить выбранные кампании (${n})? Это необратимо.`)) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteCampaigns([...selected]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <h1 className="page-title">Кампании</h1>
      <p className="page-sub">
        Excel с адресатами, общие RAG-файлы, модель с ценой. Веб-поиск — только по желанию.
      </p>

      {error && <div className="error-box">{error}</div>}

      <div className="panel">
        <h2>Новая кампания</h2>
        <form className="stack" onSubmit={onCreate}>
          <label className="field">
            Название
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Аутрейч Q3"
              required
            />
          </label>
          <label className="field">
            Модель OpenRouter (search / RAG) — цена prompt / completion
            <select
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              required={models.length > 0}
            >
              {models.length === 0 ? (
                <option value="">Модели не загружены</option>
              ) : (
                models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {modelOptionLabel(m)}
                  </option>
                ))
              )}
            </select>
          </label>
          <p className="muted" style={{ margin: 0 }}>
            Моделей с веб-поиском (tools / web_search_options): {models.length}
            {modelId ? ` · ${modelId}` : ''} · отсортированы по цене
          </p>
          <label className="field">
            Базовый промпт
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Мы предлагаем SaaS для автоматизации складского учёта. Цель письма — назначить короткий созвон."
              required
            />
          </label>
          <label className="field">
            Excel / CSV адресатов
            <input
              type="file"
              accept=".csv,.xlsx,.xls"
              onChange={(e) => setExcelFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <p className="muted" style={{ margin: 0 }}>
            Обязательна колонка <code>email</code>. Все остальные колонки идут в контекст письма.
          </p>
          <label className="field">
            RAG-файлы (общие для всех писем)
            <input
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.xls,.pptx"
              onChange={(e) => setRagFiles(e.target.files)}
            />
          </label>
          <p className="muted" style={{ margin: 0 }}>
            RAG: PDF, DOCX, PPTX, TXT, MD, CSV, XLSX/XLS. Старый Word (.doc) не поддерживается — сохраните как DOCX.
          </p>
          <label className="field">
            RAG сайтов (URL, по одному в строке)
            <textarea
              value={ragUrls}
              onChange={(e) => setRagUrls(e.target.value)}
              placeholder={'https://example.com/product\nhttps://example.com/pricing'}
              style={{ minHeight: 80 }}
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={useWebSearch}
              onChange={(e) => setUseWebSearch(e.target.checked)}
            />
            Использовать веб-поиск OpenRouter (openrouter:web_search, по умолчанию выкл.)
          </label>
          <div className="row">
            <button className="btn" type="submit" disabled={creating}>
              {creating ? 'Создание…' : 'Создать кампанию'}
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.75rem' }}>
          <h2 style={{ margin: 0 }}>Список</h2>
          {campaigns.length > 0 && (
            <div className="row">
              <span className="muted" style={{ fontSize: '0.9rem' }}>
                Выбрано: {selected.size}
              </span>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={deleting || selected.size === 0}
                onClick={() => void onDeleteSelected()}
              >
                {deleting ? 'Удаление…' : 'Удалить выбранные'}
              </button>
            </div>
          )}
        </div>
        {loading ? (
          <p className="empty">Загрузка…</p>
        ) : campaigns.length === 0 ? (
          <p className="empty">Пока нет кампаний.</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th style={{ width: 42 }}>
                    <input
                      type="checkbox"
                      checked={selected.size === campaigns.length && campaigns.length > 0}
                      onChange={toggleAll}
                      aria-label="Выбрать все"
                    />
                  </th>
                  <th>Название</th>
                  <th>Модель</th>
                  <th>Статус</th>
                  <th>Получатели</th>
                  <th>RAG</th>
                  <th>Черновики</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.id} className={selected.has(c.id) ? 'row-selected' : undefined}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.has(c.id)}
                        onChange={() => toggleOne(c.id)}
                        aria-label={`Выбрать ${c.name}`}
                      />
                    </td>
                    <td>
                      <Link className="campaign-link" to={`/campaigns/${c.id}`}>
                        {c.name}
                      </Link>
                    </td>
                    <td className="muted">{c.llm_model || '—'}</td>
                    <td>
                      <span className={`badge ${c.status}`}>{c.status}</span>
                    </td>
                    <td>{c.recipient_count}</td>
                    <td>{c.rag_file_count}</td>
                    <td>{c.draft_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
