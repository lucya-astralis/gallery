const TOKEN_KEY = 'imageslucya.admin_token';

function getToken() {
  return sessionStorage.getItem(TOKEN_KEY) || '';
}
function setToken(t) {
  if (t) sessionStorage.setItem(TOKEN_KEY, t);
  else sessionStorage.removeItem(TOKEN_KEY);
}

function adminHeaders() {
  const t = getToken();
  return t ? { 'X-Admin-Token': t } : {};
}

function showAdminUI(authed) {
  document.querySelectorAll('.admin-only').forEach((el) => {
    if (authed) el.removeAttribute('hidden');
    else el.setAttribute('hidden', '');
  });
  const loginBtn = document.getElementById('login-btn');
  const logoutBtn = document.getElementById('logout-btn');
  if (loginBtn) loginBtn.toggleAttribute('hidden', authed);
  if (logoutBtn) logoutBtn.toggleAttribute('hidden', !authed);
}

async function checkAuth() {
  try {
    const r = await fetch('/api/auth/status', { headers: adminHeaders() });
    const data = await r.json();
    if (!data.admin_enabled) {
      // No admin token configured server-side -> hide all admin UI permanently
      document.querySelectorAll('.admin-only').forEach((el) => el.setAttribute('hidden', ''));
      const loginBtn = document.getElementById('login-btn');
      const logoutBtn = document.getElementById('logout-btn');
      if (loginBtn) loginBtn.setAttribute('hidden', '');
      if (logoutBtn) logoutBtn.setAttribute('hidden', '');
      return;
    }
    if (!data.authenticated && getToken()) {
      setToken('');
    }
    showAdminUI(data.authenticated);
  } catch (e) {
    console.warn('auth check failed', e);
  }
}

function promptLogin() {
  const token = window.prompt('Admin-Token eingeben:');
  if (!token) return;
  setToken(token.trim());
  checkAuth();
}

function logout() {
  setToken('');
  showAdminUI(false);
}

function rescan() {
  const btn = document.getElementById('rescan-btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '↻ Scanne…';
  }
  fetch('/api/scan', { method: 'POST', headers: adminHeaders() })
    .then(async (r) => {
      if (r.status === 401 || r.status === 403) {
        setToken('');
        showAdminUI(false);
        throw new Error('nicht eingeloggt');
      }
      if (r.status === 429) throw new Error('Scan läuft bereits');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then((data) => {
      if (btn) {
        btn.textContent = `✓ ${data.indexed} neu, ${data.thumbnails} Thumbs`;
        setTimeout(() => {
          btn.disabled = false;
          btn.textContent = '↻ Rescan';
          window.location.reload();
        }, 1200);
      } else {
        window.location.reload();
      }
    })
    .catch((err) => {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '↻ Rescan';
      }
      alert('Scan fehlgeschlagen: ' + err.message);
    });
}

document.addEventListener('DOMContentLoaded', () => {
  checkAuth();

  const rescanBtn = document.getElementById('rescan-btn');
  if (rescanBtn) rescanBtn.addEventListener('click', rescan);
  const loginBtn = document.getElementById('login-btn');
  if (loginBtn) loginBtn.addEventListener('click', promptLogin);
  const logoutBtn = document.getElementById('logout-btn');
  if (logoutBtn) logoutBtn.addEventListener('click', logout);

  const form = document.getElementById('tag-form');
  if (form) {
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const album = form.dataset.album;
      const filename = form.dataset.filename;
      const input = document.getElementById('tag-input');
      const status = document.getElementById('tag-status');
      const fd = new FormData();
      fd.append('tags', input.value);
      status.textContent = 'speichere…';
      fetch(`/api/image/${encodeURIComponent(album)}/${encodeURIComponent(filename)}/tags`, {
        method: 'POST',
        body: fd,
        headers: adminHeaders(),
      })
        .then(async (r) => {
          if (r.status === 401 || r.status === 403) {
            setToken('');
            showAdminUI(false);
            throw new Error('nicht eingeloggt');
          }
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then((data) => {
          status.textContent = '✓ gespeichert';
          input.value = (data.tags || []).join(', ');
          setTimeout(() => {
            status.textContent = '';
            window.location.reload();
          }, 600);
        })
        .catch((err) => {
          status.textContent = 'Fehler: ' + err.message;
        });
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'ArrowLeft') {
      const prev = document.querySelector('.nav-arrow.prev');
      if (prev) window.location.href = prev.href;
    } else if (e.key === 'ArrowRight') {
      const next = document.querySelector('.nav-arrow.next');
      if (next) window.location.href = next.href;
    } else if (e.key === 'Escape') {
      const crumb = document.querySelector('.breadcrumb a:nth-last-of-type(1)');
      if (crumb) window.location.href = crumb.href;
    }
  });
});
