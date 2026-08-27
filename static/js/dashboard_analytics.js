document.addEventListener("DOMContentLoaded", () => {
  const read = (id) => {
    const node = document.getElementById(id);
    try { return node ? JSON.parse(node.textContent) : []; } catch { return []; }
  };
  const strings = document.getElementById("analytics-i18n")?.dataset || {};
  const chart = (id, labels, datasets) => {
    const canvas = document.getElementById(id);
    if (!canvas || typeof Chart === "undefined") return;
    Chart.getChart(canvas)?.destroy();
    new Chart(canvas, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { rtl: strings.direction === "rtl", textDirection: strings.direction || "ltr" } },
      },
    });
  };
  const revenue = read("revenue-chart-data");
  const revenueDates = [...new Set(revenue.map((row) => row.date))];
  const revenueCurrencies = [...new Set(revenue.map((row) => row.currency))];
  const revenueDatasets = revenueCurrencies.flatMap((currency) => {
    const values = Object.fromEntries(revenue.filter((row) => row.currency === currency).map((row) => [row.date, row]));
    return [
      { label: `${currency} ${strings.grossSales}`, data: revenueDates.map((date) => values[date]?.gross_sales ?? 0) },
      { label: `${currency} ${strings.refunds}`, data: revenueDates.map((date) => values[date]?.refunds ?? 0) },
      { label: `${currency} ${strings.netRevenue}`, data: revenueDates.map((date) => values[date]?.net_revenue ?? 0) },
    ];
  });
  chart("revenueChart", revenueDates, revenueDatasets);
  const orders = read("orders-chart-data");
  chart("ordersChart", orders.map((row) => row.date), [
    { label: strings.ordersCreated, data: orders.map((row) => row.orders_created) },
    { label: strings.paidOrders, data: orders.map((row) => row.paid_orders) },
  ]);
});
