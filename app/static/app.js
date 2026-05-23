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
