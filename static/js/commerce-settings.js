(() => {
  const updateCard = (card) => {
    const mode = card.querySelector('select');
    const amounts = card.querySelector('[data-fixed-amounts]');
    if (!mode || !amounts) return;
    amounts.hidden = mode.value !== 'fixed';
  };

  document.querySelectorAll('[data-delivery-policy-card]').forEach((card) => {
    const mode = card.querySelector('select');
    updateCard(card);
    mode?.addEventListener('change', () => updateCard(card));
  });
})();
