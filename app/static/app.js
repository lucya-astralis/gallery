document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'ArrowLeft') {
    const prev = document.querySelector('.nav-arrow.prev');
    if (prev) window.location.href = prev.href;
  } else if (e.key === 'ArrowRight') {
    const next = document.querySelector('.nav-arrow.next');
    if (next) window.location.href = next.href;
  } else if (e.key === 'Escape') {
    const crumb = document.querySelector('.crumb a:last-of-type');
    if (crumb) window.location.href = crumb.href;
  }
});

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('load-full-btn');
  const img = document.getElementById('stage-img');
  const loader = document.getElementById('stage-loader');
  const stamp = document.getElementById('quality-stamp');
  if (!btn || !img || !loader) return;

  btn.addEventListener('click', () => {
    const fullUrl = img.dataset.full;
    if (!fullUrl) return;
    loader.classList.add('is-loading');
    btn.textContent = 'Lade…';

    const full = new Image();
    full.onload = () => {
      img.src = fullUrl;
      img.classList.remove('is-preview');
      loader.classList.add('is-done');
      if (stamp) {
        stamp.textContent = 'ORIGINAL';
        stamp.classList.remove('stamp--cy');
        stamp.classList.add('stamp--ok');
      }
    };
    full.onerror = () => {
      loader.classList.remove('is-loading');
      btn.textContent = 'Fehler – nochmal?';
    };
    full.src = fullUrl;
  });
});
