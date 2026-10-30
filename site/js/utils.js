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
