'use strict';

// Loaded in <head> so the saved theme applies before first paint.

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmtInt(n) { return (n == null) ? '—' : Number(n).toLocaleString('en-GB'); }
function fmtKm(n) { return (n == null) ? '—' : `${Number(n).toLocaleString('en-GB', { maximumFractionDigits: 1 })} km`; }

// '29 Oct 2026' from an ISO timestamp, in local time.
function fmtDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

// Read a CSS custom property, so map colours follow the theme.
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Theme ---------------------------------------------------------------------
// Follows the OS by default; the toggle persists an explicit choice.
const THEME_KEY = 'monitoring_cornwall_theme';
(function () {
  try {
    const s = localStorage.getItem(THEME_KEY);
    if (s) document.documentElement.setAttribute('data-theme', s);
  } catch (e) { /* storage blocked: follow the OS */ }
})();

function isDark() {
  const cur = document.documentElement.getAttribute('data-theme');
  return cur ? cur === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
}

function initTheme() {
  const btn = $('#theme-toggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const nx = isDark() ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', nx);
    try { localStorage.setItem(THEME_KEY, nx); } catch (e) { /* ignore */ }
    if (window.onThemeChange) window.onThemeChange();
  });
  // If the OS theme flips and the user hasn't chosen one, follow it.
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!document.documentElement.getAttribute('data-theme') && window.onThemeChange) window.onThemeChange();
  });
}

async function getJSON(path) {
  const r = await fetch(path, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

// A pill in the header that opens a panel below it, like a menu. One is open
// at a time, and a click elsewhere or Escape closes it. The page-wide
// listeners are added with the first pill.
const PillMenus = (() => {
  const menus = [];
  const set = (m, open) => { m.button.setAttribute('aria-expanded', String(open)); m.panel.hidden = !open; };
  const closeAll = except => menus.forEach(m => { if (m !== except) set(m, false); });

  function add(box) {
    const button = box.querySelector('button[aria-controls]');
    const m = { button, panel: document.getElementById(button.getAttribute('aria-controls')) };
    if (!menus.length) {
      document.addEventListener('click', e => { if (!e.target.closest('.pill-menu')) closeAll(); });
      document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAll(); });
    }
    menus.push(m);
    button.addEventListener('click', e => {
      e.stopPropagation();
      const open = button.getAttribute('aria-expanded') !== 'true';
      closeAll(m);
      set(m, open);
    });
  }

  // The panel's usual parts: a heading, paragraphs, and a source line with a link.
  function fill(panel, heading, paragraphs, source, link) {
    const el = (tag, cls, text) => {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) e.textContent = text;
      return e;
    };
    panel.textContent = '';
    panel.appendChild(el('div', 'pill-panel-head', heading));
    for (const p of paragraphs) panel.appendChild(el('p', 'pill-panel-text', p));
    const src = el('div', 'pill-panel-source', source);
    if (link) {
      const a = el('a', null, link.text);
      a.href = link.href;
      a.target = '_blank';
      a.rel = 'noopener';
      src.appendChild(a);
    }
    panel.appendChild(src);
  }

  return { add, fill };
})();
