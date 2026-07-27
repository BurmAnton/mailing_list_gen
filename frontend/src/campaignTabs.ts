export const CAMPAIGN_TABS = [
  'settings',
  'recipients',
  'segments',
  'rag',
  'generate',
  'drafts',
  'integrations',
  'send',
  'dashboard',
  'export',
] as const;

export type CampaignTab = (typeof CAMPAIGN_TABS)[number];

export const CAMPAIGN_TAB_LABELS: Record<CampaignTab, string> = {
  settings: 'Настройки',
  recipients: 'Получатели',
  segments: 'Сегменты',
  rag: 'RAG',
  generate: 'Генерация',
  drafts: 'Письма',
  integrations: 'Интеграции',
  send: 'Рассылка',
  dashboard: 'Дашборд',
  export: 'Экспорт',
};

export function isCampaignTab(value: string | undefined | null): value is CampaignTab {
  return !!value && (CAMPAIGN_TABS as readonly string[]).includes(value);
}

export function parseCampaignTab(value: string | undefined | null): CampaignTab {
  return isCampaignTab(value) ? value : 'settings';
}

/** Deep-link to a campaign tab, e.g. /campaigns/1/dashboard */
export function campaignTabPath(campaignId: number | string, tab: CampaignTab = 'settings'): string {
  return `/campaigns/${campaignId}/${tab}`;
}
