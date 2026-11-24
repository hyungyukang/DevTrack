// 공통 유틸리티: 안전한 HTML 템플릿, 아이콘, 라벨, 시간 형식
import { L, translated, translatedStrings, locale } from './i18n.js';

class Raw {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
export const raw = (s) => new Raw(String(s ?? ''));

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

function renderVal(v) {
  if (v == null || v === false || v === true) return '';
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(renderVal).join('');
  return esc(v);
}

/** 보간 값을 자동으로 이스케이프하는 태그드 템플릿. raw()/html`` 결과는 그대로 삽입. */
export function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += renderVal(vals[i]) + strings[i + 1];
  return new Raw(out);
}

// Feather 스타일 아이콘 (MIT)
const ICONS = {
  slides: '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/><polyline points="7 12 10 9 13 11 17 7"/>',
  poster: '<rect x="5" y="2" width="14" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/><rect x="8" y="9" width="3.5" height="4"/><rect x="12.5" y="9" width="3.5" height="4"/><line x1="8" y1="17" x2="16" y2="17"/>',
  visual: '<rect x="3" y="3" width="7" height="5" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="8.5" y="16" width="7" height="5" rx="1"/><path d="M6.5 8v3.5h11V8M12 11.5V16"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
  list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
  bell: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
  'check-circle': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  'file-text': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>',
  file: '<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/>',
  activity: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  sun: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  package: '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  git: '<circle cx="12" cy="12" r="4"/><line x1="1.05" y1="12" x2="7" y2="12"/><line x1="17.01" y1="12" x2="22.96" y2="12"/>',
  branch: '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  'x-circle': '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
  external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  code: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  'file-search': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><circle cx="11.5" cy="14.5" r="2.5"/><line x1="13.3" y1="16.3" x2="15.5" y2="18.5"/>',
  bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><line x1="9" y1="18" x2="15" y2="18"/><line x1="10" y1="22" x2="14" y2="22"/>',
  book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  bar: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  menu: '<line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/>',
  send: '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  arrow: '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
  chev: '<polyline points="9 18 15 12 9 6"/>',
  circle: '<circle cx="12" cy="12" r="9"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  table: '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>',
  globe: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  play: '<polygon points="5 3 19 12 5 21 5 3"/>',
  pause: '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
  slash: '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
  sliders: '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  github: '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>',
  inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
};
export const icon = (name, cls = '') =>
  raw(`<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ICONS.circle}</svg>`);

// ------------------------------------------------------------------ 라벨
export const STATUS = translated({
  queued: { label: '대기', tone: 'gray', icon: 'clock' },
  in_progress: { label: '진행 중', tone: 'blue', icon: 'play' },
  awaiting_approval: { label: '승인 대기', tone: 'amber', icon: 'bell' },
  changes_requested: { label: '수정 요청§상태', tone: 'orange', icon: 'edit' },
  blocked: { label: '막힘', tone: 'red', icon: 'slash' },
  done: { label: '완료', tone: 'green', icon: 'check-circle' },
  cancelled: { label: '취소§상태', tone: 'gray', icon: 'x-circle' },
});
export const TYPES = translated({
  code: { label: '코드 개발', icon: 'code', tone: 'blue' },
  review: { label: '코드 리뷰', icon: 'eye', tone: 'violet' },
  paper: { label: '논문 작성', icon: 'book', tone: 'teal' },
  slides: { label: '발표 자료', icon: 'slides', tone: 'blue' },
  poster: { label: '포스터§유형', icon: 'poster', tone: 'teal' },
  docreview: { label: '문서 리뷰', icon: 'file-search', tone: 'orange' },
  visual: { label: '문서 시각화', icon: 'visual', tone: 'pink' },
  analysis: { label: '데이터 분석', icon: 'bar', tone: 'amber' },
  visualization: { label: '시각화', icon: 'image', tone: 'pink' },
  model: { label: 'AI 모델', icon: 'cpu', tone: 'accent' },
  brainstorm: { label: '브레인스토밍', icon: 'bulb', tone: 'green' },
  other: { label: '기타', icon: 'file', tone: 'gray' },
});
export const APPROVAL_STATUS = translated({
  pending: { label: '결정 대기', tone: 'amber', icon: 'clock' },
  approved: { label: '승인됨', tone: 'green', icon: 'check-circle' },
  changes_requested: { label: '수정 요청됨', tone: 'orange', icon: 'edit' },
  rejected: { label: '거절됨', tone: 'red', icon: 'x-circle' },
  withdrawn: { label: '철회됨', tone: 'gray', icon: 'slash' }, // 작업 취소 · 완료 처리로 — 이유는 comment 에
});
export const APPROVAL_KIND = translated({
  commit: { label: '커밋 · 푸시', icon: 'git' },
  plan: { label: '작업 계획', icon: 'list' },
  review: { label: '리뷰 확인', icon: 'eye' },
  result: { label: '결과물 확인', icon: 'package' },
  merge: { label: '병합', icon: 'branch' },
  general: { label: '일반', icon: 'check-circle' },
});
export const LOG_KIND = translated({
  info: { label: '정보', icon: 'info', tone: 'gray' },
  step: { label: '진행', icon: 'arrow', tone: 'blue' },
  success: { label: '완료', icon: 'check', tone: 'green' },
  warning: { label: '경고', icon: 'alert', tone: 'amber' },
  error: { label: '오류', icon: 'x-circle', tone: 'red' },
  issue: { label: '문제', icon: 'alert', tone: 'orange' },
  user: { label: '사용자', icon: 'message', tone: 'accent' },
  approval: { label: '승인 요청§하나', icon: 'bell', tone: 'amber' },
  decision: { label: '결정', icon: 'check-circle', tone: 'green' },
  commit: { label: '커밋', icon: 'git', tone: 'violet' },
  artifact: { label: '결과물', icon: 'package', tone: 'teal' },
  status: { label: '상태', icon: 'circle', tone: 'gray' },
  agent: { label: 'AI 메시지', icon: 'cpu', tone: 'violet' },
});
/** 리뷰 반복의 상태 (store.REVIEW_STATES) */
export const REVIEW_STATE = translated({
  planned: { label: '리뷰 전', tone: 'gray', icon: 'clock' },
  reviewing: { label: '리뷰 중', tone: 'blue', icon: 'eye' },
  changes: { label: '수정 필요§리뷰', tone: 'amber', icon: 'edit' },
  approved: { label: '통과', tone: 'green', icon: 'check-circle' },
  exhausted: { label: '리뷰 횟수 다 씀', tone: 'red', icon: 'alert' },
  rejected: { label: '게재 불가', tone: 'red', icon: 'x-circle' },
});
export const SEVERITY = translated({
  low: { label: '낮음', tone: 'gray' },
  medium: { label: '보통§심각도', tone: 'amber' },
  high: { label: '높음', tone: 'orange' },
  critical: { label: '심각', tone: 'red' },
});
// 코드 리뷰 검토 항목 (review-pr 스킬의 Blocker / Major / Minor / Nit / Question)
export const FINDING_SEV = translated({
  blocker: { label: '반드시 수정', en: 'Blocker', tone: 'red', hint: '병합 전에 꼭 고쳐야 하는 문제' },
  major: { label: '수정 권장', en: 'Major', tone: 'orange', hint: '결과나 동작에 영향을 줄 수 있는 문제' },
  minor: { label: '개선 제안', en: 'Minor', tone: 'amber', hint: '고치면 더 좋아지는 부분' },
  nit: { label: '사소함', en: 'Nit', tone: 'gray', hint: '표기 · 스타일 수준' },
  question: { label: '질문', en: 'Question', tone: 'blue', hint: '작성자에게 확인이 필요한 부분' },
});
export const FINDING_STATUS = translated({
  open: { label: '수정 필요', tone: 'orange' },
  fixed: { label: '해결됨', tone: 'green' },
  commented: { label: 'PR에 게시', tone: 'violet' },
  dismissed: { label: '제외', tone: 'gray' },
  na: { label: '해당 없음', tone: 'gray' },
});
export const VERDICT = translated({
  approve: { label: '승인해도 됨', tone: 'green', icon: 'check-circle' },
  comment: { label: '의견만 전달', tone: 'blue', icon: 'message' },
  request_changes: { label: '수정 후 다시 확인', tone: 'orange', icon: 'edit' },
  // 문서 리뷰의 종합 의견
  ready: { label: '제출 가능', tone: 'green', icon: 'check-circle' },
  minor_revision: { label: '소폭 수정', tone: 'blue', icon: 'edit' },
  major_revision: { label: '대폭 보완', tone: 'orange', icon: 'alert' },
  rework: { label: '다시 작성', tone: 'red', icon: 'x-circle' },
});
// 문서 리뷰: 문서 종류 · 점검 영역 평가
export const DOC_KIND = translated({
  paper: { label: '과학 논문' },
  report: { label: '보고서' },
  proposal: { label: '제안서' },
  thesis: { label: '학위 논문' },
  other: { label: '기타 문서' },
});
export const CHECK_RATING = translated({
  good: { label: '양호', tone: 'green', icon: 'check' },
  fair: { label: '보완 필요', tone: 'amber', icon: 'alert' },
  poor: { label: '미흡', tone: 'red', icon: 'x-circle' },
  na: { label: '해당 없음', tone: 'gray', icon: 'slash' },
});
export const CONFIDENCE = translatedStrings({ high: '높음', medium: '중간', low: '낮음' });

export const ART_KIND = translated({
  markdown: { label: '문서§종류', icon: 'file-text' },
  pdf: { label: 'PDF', icon: 'file-text' },
  docx: { label: 'Word', icon: 'file-text' },
  pptx: { label: 'PowerPoint', icon: 'file' },
  image: { label: '그림§종류', icon: 'image' },
  diff: { label: '변경사항', icon: 'git' },
  code: { label: '코드', icon: 'code' },
  latex: { label: 'LaTeX', icon: 'book' },
  notebook: { label: '노트북', icon: 'book' },
  csv: { label: '데이터', icon: 'table' },
  html: { label: 'HTML', icon: 'globe' },
  text: { label: '텍스트', icon: 'file' },
  link: { label: '링크', icon: 'link' },
  file: { label: '파일', icon: 'file' },
});

export const pick = (map, key, fallback = 'other') => map[key] || map[fallback] || { label: key, tone: 'gray', icon: 'circle' };

export function badge(map, key, { withIcon = false, dot = false } = {}) {
  const m = pick(map, key);
  return html`<span class="badge tone-${m.tone || 'gray'}">${dot ? raw('<span class="dot"></span>') : ''}${withIcon && m.icon ? icon(m.icon) : ''}${m.label}</span>`;
}

// ------------------------------------------------------------------ 시간 / 크기
// 화면 언어(i18n.js)에 맞춘 날짜 · 시간 형식 — 언어마다 한 번만 만든다
const FORMATS = {};
function fmt(kind) {
  const loc = locale();
  const key = `${loc}:${kind}`;
  if (!(key in FORMATS)) {
    FORMATS[key] = kind === 'rel'
      ? (typeof Intl !== 'undefined' && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat(loc, { numeric: 'auto' }) : null)
      : kind === 'abs'
        ? new Intl.DateTimeFormat(loc, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
        : new Intl.DateTimeFormat(loc, { month: 'short', day: 'numeric' });
  }
  return FORMATS[key];
}

export function fmtAbs(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : fmt('abs').format(d);
}
export function fmtRel(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const s = (d.getTime() - Date.now()) / 1000;
  const a = Math.abs(s);
  const rtf = fmt('rel');
  if (!rtf) return fmtAbs(iso);
  if (a < 45) return L('방금 전');
  if (a < 3600) return rtf.format(Math.round(s / 60), 'minute');
  if (a < 86400) return rtf.format(Math.round(s / 3600), 'hour');
  if (a < 86400 * 7) return rtf.format(Math.round(s / 86400), 'day');
  return fmt('day').format(d);
}
/** 상대 시간 <time> (주기적으로 갱신됨) */
export const ago = (iso) => html`<time data-ts="${iso || ''}" title="${fmtAbs(iso)}">${fmtRel(iso)}</time>`;

export function fmtBytes(n) {
  if (n == null) return '';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

export const authorName = (a) => (a === 'user' ? L('사용자') : a || L('에이전트'));

// 담당 AI — '누구나'(정해지지 않음)면 먼저 시작한 AI 가 맡는다 (devtrack/store.py 의 UNASSIGNED_AGENTS 와 같게)
const UNASSIGNED_AGENTS = ['', '누구나', '기타', 'any', '*'];
export const isUnassigned = (a) => UNASSIGNED_AGENTS.includes(String(a ?? '').trim().toLowerCase());
/** 문장에 넣을 담당 AI 이름: 'Codex에게 보낼 메시지' / 정해지지 않았으면 'AI' */
export const aiName = (t) => (isUnassigned(t?.agent) ? 'AI' : t.agent);
/** 목록 · 머리글에 보여 줄 담당 AI */
export const agentLabel = (t) => (isUnassigned(t?.agent) ? L('담당 미정') : t.agent);

// ---------------------------------------------------------------- 작업별 AI 모델 · 추론 강도 (devtrack/models.py 와 같은 규칙)
const CLAUDE_EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']; // claude --effort
const CODEX_ALL = [...CLAUDE_EFFORTS, 'ultra'];
const CODEX_NO_ULTRA = CLAUDE_EFFORTS;
/** 모델 선택 목록. index.json 의 models (devtrack/models.py 의 catalog() — Codex 는 그 컴퓨터의 Codex 가 받아 둔 목록)를 받으면
 *  setModelCatalog 로 바꾼다. 아래는 index.json 에 목록이 없을 때 쓰는 기본값 */
export const MODELS = {
  claude: [['claude-fable-5-2', 'Fable 5.2'], ['claude-opus-5-5', 'Opus 5.5'], ['claude-sonnet-5-5', 'Sonnet 5.5'],
    ['claude-haiku-5-5', 'Haiku 5.5']].map(([id, label]) => ({ id, label, efforts: CLAUDE_EFFORTS })),
  codex: [['gpt-6.1-sol', 'GPT-6.1-Sol', CODEX_ALL], ['gpt-6-astra', 'GPT-6-Astra', CODEX_ALL], ['gpt-6.1-terra', 'GPT-6.1 Terra', CODEX_ALL],
    ['gpt-6-sol', 'GPT-6-Sol', CODEX_ALL], ['gpt-6-luna', 'GPT-6-Luna', CODEX_NO_ULTRA], ['gpt-5.6-sol', 'GPT-5.6-Sol', CODEX_ALL],
    ['gpt-5.6-terra', 'GPT-5.6-Terra', CODEX_ALL], ['gpt-5.6-luna', 'GPT-5.6-Luna', CODEX_NO_ULTRA]].map(([id, label, efforts]) => ({ id, label, efforts })),
};
export function setModelCatalog(cat) {
  const clean = (list) => (Array.isArray(list) ? list : [])
    .filter((m) => m && typeof m.id === 'string' && m.id)
    .map((m) => ({ id: m.id, label: String(m.label || m.id), efforts: (Array.isArray(m.efforts) ? m.efforts : []).filter((e) => /^[a-z]{1,16}$/.test(e)) }));
  for (const key of ['claude', 'codex']) {
    const list = clean(cat?.[key]);
    if (list.length) MODELS[key] = list;
  }
}
const CLAUDE_FAMILIES = ['fable', 'opus', 'sonnet', 'haiku'];
// Claude Code: low – max, Codex: low – ultra (예전 Codex 모델은 minimal 도 받는다). 설정 이름 그대로 보여 준다
const EFFORT_NAMES = { minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra high', max: 'Max', ultra: 'Ultra' };
const knownEfforts = () => {
  const all = Object.keys(EFFORT_NAMES);
  for (const m of MODELS.codex) for (const e of m.efforts) if (!all.includes(e)) all.push(e);
  return all;
};
export const effortLabel = (e) => EFFORT_NAMES[e] || (e ? e.charAt(0).toUpperCase() + e.slice(1) : '');
const isClaudeModel = (m) => CLAUDE_FAMILIES.includes(m) || m.startsWith('claude-');
/** 이 모델 · 담당 AI 가 받는 추론 강도 [[값, 이름]] (models.allowed_efforts 와 같은 규칙):
 *  Claude 모델 · Claude 담당은 claude --effort 의 값, 목록에 있는 Codex 모델은 그 모델이 받는 값,
 *  모델을 모르는 Codex 담당은 Codex 목록의 값 + minimal, 그 밖에는 모든 값 */
export function effortsFor(agent, model) {
  const m = String(model ?? '').trim().toLowerCase();
  const a = String(agent ?? '').trim().toLowerCase();
  let list = null;
  if (m && isClaudeModel(m)) list = CLAUDE_EFFORTS;
  else if (m) list = MODELS.codex.find((x) => x.id.toLowerCase() === m && x.efforts.length)?.efforts || null;
  if (!list && a === 'claude') list = CLAUDE_EFFORTS;
  if (!list && a === 'codex') {
    const union = new Set(['minimal', ...MODELS.codex.flatMap((x) => x.efforts)]);
    list = knownEfforts().filter((e) => union.has(e));
  }
  return (list || knownEfforts()).map((e) => [e, effortLabel(e)]);
}

const squash = (v) => String(v ?? '').toLowerCase().replace(/[\s_-]+/g, '');
/** 'Opus 5.5' · 'opus-5.5' → claude-opus-5-5, 'opus' 는 그대로 (최신 Opus 별칭), Codex 목록의 표시 이름 'GPT-6.1 Terra' → gpt-6.1-terra,
 *  다른 이름은 그대로. 지정 없음이면 '' */
export function normalizeModel(name) {
  const s = String(name ?? '').trim();
  if (['', '-', 'none', 'default', 'any', '*'].includes(s.toLowerCase())) return '';
  const m = /^(?:claude[\s_-]*)?(fable|opus|sonnet|haiku)(?:[\s_-]*(\d+)(?:[\s._-]+(\d+))?)?(.*)$/.exec(s.toLowerCase());
  if (m && (m[2] || !m[4])) return m[2] ? `claude-${m[1]}-${m[2]}${m[3] ? '-' + m[3] : ''}${m[4]}` : m[1];
  const key = squash(s);
  const codex = MODELS.codex.find((x) => squash(x.id) === key || squash(x.label) === key);
  return codex ? codex.id : s;
}
export const modelLabel = (m) => {
  const low = String(m ?? '').toLowerCase();
  const hit = MODELS.claude.find((x) => x.id === m) || (!isClaudeModel(low) && MODELS.codex.find((x) => x.id.toLowerCase() === low));
  return hit ? hit.label : m || '';
};
/** 'Opus 5.5 · Extra high' (지정 없으면 '') */
export const specLabel = (model, effort) => [model ? modelLabel(model) : '', effort ? effortLabel(effort) : ''].filter(Boolean).join(' · ');

/** 지정한 모델과 세션 모델이 맞는지 — true / false, 세션 모델을 모르면 null */
export function modelMatches(want, have) {
  if (!want) return true;
  if (!have) return null;
  const w = normalizeModel(want).toLowerCase();
  const h = normalizeModel(have).toLowerCase();
  if (w === h) return true;
  if (h.startsWith(w) && ['-', '['].includes(h.charAt(w.length))) return true;
  if (CLAUDE_FAMILIES.includes(w)) return new RegExp(`^claude-${w}(-|\\[|$)`).test(h);
  return false;
}
/** 작업(t.model · t.effort)과 기다리는 AI(l.model · l.effort)가 맞는지 — true / false / null(모름). l.model '*' = 작업마다 지정 모델로 실행 */
export function specMatches(t, l) {
  if (!t?.model && !t?.effort) return true;
  if (l?.model === '*') return true;
  const r = [];
  if (t.model) r.push(modelMatches(t.model, l?.model));
  if (t.effort) r.push(l?.effort ? l.effort === t.effort : null);
  if (r.includes(false)) return false;
  return r.includes(null) ? null : true;
}

export function shellQuote(s) {
  s = String(s ?? '');
  if (/^[A-Za-z0-9_./:=@%+-]+$/.test(s)) return s;
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* ignore */ }
    ta.remove();
    return ok;
  }
}

export const LS = {
  get(k, d) {
    try { const v = localStorage.getItem('devtrack.' + k); return v == null ? d : JSON.parse(v); } catch { return d; }
  },
  set(k, v) {
    try { localStorage.setItem('devtrack.' + k, JSON.stringify(v)); } catch { /* 저장소 사용 불가 */ }
  },
};

export const tsNum = (iso) => { const t = Date.parse(iso); return isNaN(t) ? 0 : t; };
