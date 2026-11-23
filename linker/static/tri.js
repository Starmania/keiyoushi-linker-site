// Tri-state filters (macro tri_filter): a click cycles a value ignored → included → excluded, like Mihon's filters.
const NEXT = { 0: 1, 1: -1, '-1': 0 };
const SAY = { 0: 'ignored', 1: 'included', '-1': 'excluded' };

for (const item of document.querySelectorAll('.tri-item')) {
  item.addEventListener('click', () => {
    const state = NEXT[item.dataset.state];
    const input = item.querySelector('input');
    item.dataset.state = state;
    input.disabled = state === 0;
    input.value = (state === -1 ? '-' : '') + item.dataset.value;
    item.setAttribute('aria-label', `${item.querySelector('.grow').textContent}: ${SAY[state]}`);
    count(item.closest('.tri'));
  });
}

function count(box) {
  const n = (s) => box.querySelectorAll(`.tri-item[data-state="${s}"]`).length;
  const inc = n(1), exc = n(-1);
  box.querySelector('.tri-n').innerHTML =
    (inc ? ` <span class="inc">✓${inc}</span>` : '') + (exc ? ` <span class="exc">✗${exc}</span>` : '');
}

// Only one list open at a time; a click elsewhere closes it.
document.addEventListener('click', (e) => {
  for (const d of document.querySelectorAll('details.tri[open]')) if (!d.contains(e.target)) d.open = false;
});
