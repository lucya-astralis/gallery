function rescan() {
  const btn = document.getElementById('rescan-btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '↻ Scanne…';
  }
  fetch('/api/scan', { method: 'POST' })
    .then((r) => r.json())
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
      alert('Scan fehlgeschlagen: ' + err);
    });
}

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('rescan-btn');
  if (btn) btn.addEventListener('click', rescan);

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
      })
        .then((r) => {
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
