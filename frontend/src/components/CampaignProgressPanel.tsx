import { useEffect, useMemo, useState } from 'react';
import type {
  CampaignStatus,
  CampaignStatusOut,
  Draft,
  DraftStatus,
  Recipient,
  SegmentProgress,
} from '../api';

const STATUS_LABEL: Record<DraftStatus | CampaignStatus, string> = {
  draft: 'Черновик',
  pending: 'В очереди',
  generating: 'Генерируется',
  ready: 'Готово',
  edited: 'Отредактировано',
  error: 'Ошибка',
};

function formatDuration(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m <= 0) return `${s} с`;
  return `${m} мин ${s.toString().padStart(2, '0')} с`;
}

function segmentPct(seg: SegmentProgress): number {
  if (seg.total <= 0) return 0;
  return Math.round(((seg.ready + seg.edited + seg.error) / seg.total) * 100);
}

function recipientSegmentValue(recipient: Recipient | null | undefined, field: string | null | undefined): string {
  if (!recipient || !field) return '';
  const typed: Record<string, string | null | undefined> = {
    industry: recipient.industry,
    company_name: recipient.company_name,
    description: recipient.description,
    website_url: recipient.website_url,
    name: recipient.name,
  };
  if (field in typed) return String(typed[field] ?? '').trim();
  const extra = recipient.extra_fields?.[field];
  return extra != null ? String(extra).trim() : '';
}

type Props = {
  campaignStatus: CampaignStatus;
  status: CampaignStatusOut | null;
  drafts: Draft[];
  startedAt: number | null;
  filter: DraftStatus | 'all';
  onFilterChange: (f: DraftStatus | 'all') => void;
  onStop?: () => void;
  stopping?: boolean;
  segmentField?: string | null;
};

export function CampaignProgressPanel({
  campaignStatus,
  status,
  drafts,
  startedAt,
  filter,
  onFilterChange,
  onStop,
  stopping,
  segmentField,
}: Props) {
  const [now, setNow] = useState(Date.now());
  const [segmentFilter, setSegmentFilter] = useState<string | null>(null);

  useEffect(() => {
    if (campaignStatus !== 'generating') return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [campaignStatus]);

  const total = status?.total ?? drafts.length;
  const ready = status?.ready ?? 0;
  const edited = status?.edited ?? 0;
  const error = status?.error ?? 0;
  const pending = status?.pending ?? 0;
  const generating = status?.generating ?? 0;
  const success = ready + edited;
  const processed = success + error;
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
  const segmentRows = status?.segments ?? [];
  const showSegments = !!status?.use_segment_templates && segmentRows.length > 0;

  const elapsedMs = startedAt ? now - startedAt : 0;
  const etaMs = useMemo(() => {
    if (!startedAt || processed <= 0 || processed >= total || campaignStatus !== 'generating') {
      return null;
    }
    const perItem = elapsedMs / processed;
    return Math.round(perItem * (total - processed));
  }, [startedAt, processed, total, campaignStatus, elapsedMs]);

  const stage =
    campaignStatus === 'generating'
      ? generating > 0 || pending > 0
        ? 'letters'
        : 'prepare'
      : campaignStatus === 'ready'
        ? 'done'
        : 'idle';

  const filtered = useMemo(() => {
    let list = filter === 'all' ? drafts : drafts.filter((d) => d.status === filter);
    if (segmentFilter != null && showSegments) {
      if (segmentFilter === '__other__') {
        const known = new Set(segmentRows.map((s) => s.value).filter((v) => v !== '__other__'));
        list = list.filter((d) => {
          const v = recipientSegmentValue(d.recipient, segmentField);
          return !known.has(v);
        });
      } else {
        list = list.filter(
          (d) => recipientSegmentValue(d.recipient, segmentField) === segmentFilter,
        );
      }
    }
    return list;
  }, [drafts, filter, segmentFilter, showSegments, segmentRows, segmentField]);

  if (total === 0 && campaignStatus === 'draft') {
    return (
      <div className="progress-panel progress-panel--side">
        <div className="progress-panel-head">
          <div>
            <h2 style={{ margin: 0 }}>Прогресс</h2>
            <p className="muted" style={{ margin: '0.25rem 0 0' }}>
              Загрузите получателей и запустите генерацию — здесь появится статус писем.
            </p>
          </div>
          <div className="progress-pct" aria-hidden>
            —
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`progress-panel progress-panel--side ${campaignStatus === 'generating' ? 'is-live' : ''}`}
    >
      <div className="progress-panel-head">
        <div>
          <h2 style={{ margin: 0 }}>Прогресс кампании</h2>
          <p className="muted" style={{ margin: '0.25rem 0 0' }}>
            {campaignStatus === 'generating'
              ? stopping
                ? 'Остановка… текущие запросы завершатся, новые не стартуют'
                : 'Генерация идёт — статус обновляется автоматически'
              : campaignStatus === 'ready'
                ? error > 0
                  ? 'Кампания завершена с ошибками'
                  : 'Кампания завершена'
                : 'Черновики ещё не созданы или генерация не запускалась'}
          </p>
        </div>
        <div className="progress-head-actions">
          {campaignStatus === 'generating' && onStop && (
            <button
              type="button"
              className="btn btn-ghost"
              disabled={stopping}
              onClick={onStop}
            >
              {stopping ? 'Остановка…' : 'Стоп'}
            </button>
          )}
          <div className="progress-pct" aria-live="polite">
            {pct}%
          </div>
        </div>
      </div>

      <ol className="progress-stages">
        <li className={stage !== 'idle' ? 'done' : ''}>Подготовка</li>
        <li
          className={
            stage === 'letters' ? 'active' : stage === 'done' ? 'done' : ''
          }
        >
          Письма
        </li>
        <li className={stage === 'done' ? 'done' : ''}>Готово</li>
      </ol>

      <div className="progress-bar progress-bar-lg" aria-label="Прогресс генерации">
        {total > 0 && (
          <>
            <span
              className="seg-ok"
              style={{ width: `${(success / total) * 100}%` }}
              title={`Готово: ${success}`}
            />
            <span
              className="seg-err"
              style={{ width: `${(error / total) * 100}%` }}
              title={`Ошибки: ${error}`}
            />
            <span
              className="seg-run"
              style={{ width: `${(generating / total) * 100}%` }}
              title={`В работе: ${generating}`}
            />
          </>
        )}
      </div>

      <div className="progress-stats">
        <button
          type="button"
          className={`stat ${filter === 'all' ? 'active' : ''}`}
          onClick={() => onFilterChange('all')}
        >
          <strong>{total}</strong>
          <span>всего</span>
        </button>
        <button
          type="button"
          className={`stat ok ${filter === 'ready' ? 'active' : ''}`}
          onClick={() => onFilterChange('ready')}
        >
          <strong>{ready}</strong>
          <span>готово</span>
        </button>
        <button
          type="button"
          className={`stat warn ${filter === 'generating' || filter === 'pending' ? 'active' : ''}`}
          onClick={() => onFilterChange(generating > 0 ? 'generating' : 'pending')}
        >
          <strong>{pending + generating}</strong>
          <span>в очереди / работе</span>
        </button>
        <button
          type="button"
          className={`stat danger ${filter === 'error' ? 'active' : ''}`}
          onClick={() => onFilterChange('error')}
        >
          <strong>{error}</strong>
          <span>ошибок</span>
        </button>
        <button
          type="button"
          className={`stat ${filter === 'edited' ? 'active' : ''}`}
          onClick={() => onFilterChange('edited')}
        >
          <strong>{edited}</strong>
          <span>правки</span>
        </button>
      </div>

      {showSegments && (
        <div className="segment-progress">
          <div className="segment-progress__head">
            <h3>По сегментам</h3>
            {segmentFilter != null && (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setSegmentFilter(null)}
              >
                Сбросить
              </button>
            )}
          </div>
          <ul className="segment-progress__list">
            {segmentRows.map((seg) => {
              const done = seg.ready + seg.edited;
              const sp = segmentPct(seg);
              const active = segmentFilter === seg.value;
              return (
                <li key={seg.segment_id ?? seg.value}>
                  <button
                    type="button"
                    className={`segment-progress__row ${active ? 'is-active' : ''}`}
                    onClick={() =>
                      setSegmentFilter((prev) => (prev === seg.value ? null : seg.value))
                    }
                    title="Показать письма сегмента"
                  >
                    <div className="segment-progress__meta">
                      <span className="segment-progress__label">{seg.label}</span>
                      <span className="muted">
                        {done + seg.error}/{seg.total} · {sp}%
                        {seg.generating > 0 ? ` · ${seg.generating} в работе` : ''}
                        {seg.error > 0 ? ` · ${seg.error} ош.` : ''}
                      </span>
                    </div>
                    <div className="progress-bar segment-progress__bar" aria-hidden>
                      {seg.total > 0 && (
                        <>
                          <span
                            className="seg-ok"
                            style={{ width: `${(done / seg.total) * 100}%` }}
                          />
                          <span
                            className="seg-err"
                            style={{ width: `${(seg.error / seg.total) * 100}%` }}
                          />
                          <span
                            className="seg-run"
                            style={{ width: `${(seg.generating / seg.total) * 100}%` }}
                          />
                        </>
                      )}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {(campaignStatus === 'generating' || startedAt) && (
        <p className="muted progress-meta">
          {campaignStatus === 'generating' && (
            <>
              Прошло: <strong>{formatDuration(elapsedMs)}</strong>
              {etaMs != null && (
                <>
                  {' '}
                  · осталось ≈ <strong>{formatDuration(etaMs)}</strong>
                </>
              )}
              {' · '}
            </>
          )}
          Обработано {processed} из {total}
        </p>
      )}

      {drafts.length > 0 && (
        <div className="progress-table-wrap">
          <table className="data progress-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Получатель</th>
                <th>Компания</th>
                <th>Статус</th>
                <th>Тема / ошибка</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={5} className="muted">
                    Нет записей в этом фильтре
                  </td>
                </tr>
              ) : (
                filtered.map((d, i) => (
                  <tr
                    key={d.id}
                    className={`row-status-${d.status}`}
                    id={`draft-row-${d.id}`}
                  >
                    <td>{i + 1}</td>
                    <td>{d.recipient?.email ?? '—'}</td>
                    <td>{d.recipient?.company_name ?? '—'}</td>
                    <td>
                      <span className={`badge ${d.status}`}>
                        {STATUS_LABEL[d.status]}
                      </span>
                    </td>
                    <td className={d.status === 'error' ? 'text-danger' : 'muted'}>
                      {d.status === 'error'
                        ? d.error || 'Ошибка'
                        : d.subject || (d.status === 'generating' ? '…' : '—')}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
