'use strict';

const catalog = {
  controls: document.getElementById('catalog-controls'),
  search: document.getElementById('catalog-search'),
  buttons: [...document.querySelectorAll('#catalog-filters button')],
  reset: document.getElementById('catalog-reset'),
  retry: document.getElementById('catalog-retry'),
  count: document.getElementById('catalog-count'),
  status: document.getElementById('catalog-status'),
  cards: document.getElementById('catalog-cards'),
};
const exams = new Set(['ОГЭ', 'ЕГЭ']);
const socialLabels = { vk: 'ВКонтакте', instagram: 'Instagram', youtube: 'YouTube' };
const nothingFound = 'Материалы не найдены. Попробуйте изменить поисковый запрос или сбросить фильтры.';
let entries = [];
let selectedExam = '';
let invalidCount = 0;

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function safeLink(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
    return url;
  } catch {
    return null;
  }
}

function trainerPath(slug) {
  if (typeof slug !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) ||
      /[^a-z0-9-]/.test(slug)) return null;
  return './simulator/' + slug + '/';
}

function imageLink(value, path) {
  const expected = path + 'preview.webp';
  return path && value === expected ? new URL(expected, document.baseURI) : null;
}

function strings(value) {
  return Array.isArray(value) && value.every(item => typeof item === 'string' && item.trim());
}

function normalize(value) {
  return value.toLocaleLowerCase('ru').normalize('NFC').replaceAll('ё', 'е').trim().replace(/\s+/g, ' ');
}

function parseEntry(value) {
  if (!value || typeof value !== 'object') return null;
  const { id, slug, number, title, description, category, features, keywords, imageAlt, metrics } = value;
  const path = trainerPath(slug);
  const url = path && value.url === path ? new URL(path, document.baseURI) : null;
  const image = imageLink(value.image, path);
  if (!strings([id, slug, title, description, category]) ||
      !Number.isSafeInteger(number) || number < 1 || !url || !image ||
      !strings(value.exams) || !value.exams.length || !value.exams.every(exam => exams.has(exam)) ||
      !strings(features) || !strings(keywords) ||
      !Array.isArray(metrics) || !metrics.every(metric =>
        metric && Number.isFinite(metric.value) && strings([metric.label]))) return null;
  const numberLabel = String(number).padStart(2, '0');
  const searchText = normalize([title, description, category, ...keywords].join(' '));
  return {
    id, number, numberLabel, title, description, category, features, metrics,
    exams: value.exams, url, image, imageAlt: typeof imageAlt === 'string' ? imageAlt : '', searchText,
  };
}

function makeCard(entry) {
  const card = element('article', 'card');
  const visual = element('div', 'card-visual');
  const image = element('img');
  image.width = 960;
  image.height = 540;
  image.alt = entry.imageAlt;
  image.loading = 'lazy';
  image.decoding = 'async';
  image.addEventListener('error', () => {
    visual.replaceChildren(element('span', '', 'Изображение недоступно'));
  }, { once: true });
  visual.append(image);
  image.src = entry.image.href;

  const body = element('div', 'card-body');
  body.append(
    element('p', 'eyebrow', entry.numberLabel + ' / ' + entry.category),
    element('h3', '', entry.title),
    element('p', 'card-description', entry.description),
  );

  const tags = element('ul', 'tags');
  tags.setAttribute('aria-label', 'Экзамены и форматы');
  for (const tag of [...new Set([...entry.exams, ...entry.features])]) {
    tags.append(element('li', '', tag));
  }
  body.append(tags);

  if (entry.metrics.length) {
    const metrics = element('ul', 'metrics');
    metrics.setAttribute('aria-label', 'Возможности тренажёра');
    for (const metric of entry.metrics) {
      const item = element('li');
      item.append(element('strong', '', String(metric.value)), document.createTextNode(metric.label));
      metrics.append(item);
    }
    body.append(metrics);
  }

  const link = element('a', 'button primary', 'Открыть тренажёр');
  const arrow = element('span', '', '→');
  arrow.setAttribute('aria-hidden', 'true');
  link.append(arrow);
  link.setAttribute('aria-label', 'Открыть тренажёр «' + entry.title + '»');
  link.href = entry.url.href;
  body.append(link);
  card.append(visual, body);
  return card;
}

function render() {
  const query = normalize(catalog.search.value);
  const terms = query ? query.split(' ') : [];
  const found = entries.filter(entry =>
    (!selectedExam || entry.exams.includes(selectedExam)) &&
    terms.every(term => /^\d+$/.test(term) ? Number(term) === entry.number : entry.searchText.includes(term)));
  catalog.count.textContent = 'Найдено: ' + found.length;
  catalog.reset.hidden = !query && !selectedExam;
  for (const button of catalog.buttons) {
    button.setAttribute('aria-pressed', String(button.dataset.exam === selectedExam));
  }
  catalog.cards.replaceChildren(...found.map(makeCard));

  const messages = [];
  if (!found.length) {
    messages.push(entries.length ? nothingFound : 'Доступных тренажёров пока нет.');
  }
  if (invalidCount) messages.push('Некоторые тренажёры недоступны из-за ошибок в каталоге.');
  catalog.status.textContent = messages.join(' ');
}

async function readJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}

async function loadCatalog() {
  catalog.retry.hidden = true;
  catalog.status.textContent = 'Загружаем тренажёры…';
  try {
    const data = await readJson('./trainers.json');
    if (!Array.isArray(data)) throw new Error('Неверный формат каталога');
    const acceptedIds = new Set();
    invalidCount = 0;
    entries = [];
    for (const item of data) {
      if (item?.status === 'draft' || item?.status === 'planned') continue;
      const entry = item?.status === 'published' ? parseEntry(item) : null;
      if (!entry || acceptedIds.has(entry.id)) {
        invalidCount++;
        continue;
      }
      acceptedIds.add(entry.id);
      entries.push(entry);
    }
    entries.sort((a, b) => a.number - b.number);
    catalog.controls.hidden = false;
    render();
  } catch {
    catalog.controls.hidden = true;
    catalog.cards.replaceChildren();
    catalog.count.textContent = '';
    catalog.status.textContent = 'Не удалось загрузить каталог. Повторите попытку.';
    catalog.retry.hidden = false;
  }
}

async function loadSocial() {
  try {
    const data = await readJson('./social.json');
    const region = document.getElementById('social');
    for (const [key, label] of Object.entries(socialLabels)) {
      const url = safeLink(data?.[key]);
      if (!url || url.protocol !== 'https:') continue;
      const link = element('a', '', label);
      link.href = url.href;
      region.append(link);
    }
    if (!region.childElementCount) return;
    region.hidden = false;
    for (const link of document.querySelectorAll('[data-social-nav]')) link.hidden = false;
  } catch {
    // Social links are optional and independent of the trainer registry.
  }
}

catalog.search.addEventListener('input', render);
for (const button of catalog.buttons) {
  button.addEventListener('click', () => {
    selectedExam = button.dataset.exam;
    render();
  });
}
catalog.reset.addEventListener('click', () => {
  catalog.search.value = '';
  selectedExam = '';
  catalog.search.focus();
  render();
});
catalog.retry.addEventListener('click', loadCatalog);
const mobileMenu = document.querySelector('.mobile-nav');
for (const link of mobileMenu.querySelectorAll('a')) {
  link.addEventListener('click', () => {
    mobileMenu.open = false;
    mobileMenu.querySelector('summary').focus({ preventScroll: true });
  });
}
mobileMenu.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || !mobileMenu.open) return;
  mobileMenu.open = false;
  mobileMenu.querySelector('summary').focus();
});
loadCatalog();
loadSocial();
