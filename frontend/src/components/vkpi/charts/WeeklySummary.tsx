export function WeeklySummary({ summary }: { summary: string }) {
  return (
    <section className="vkpi-card vkpi-panel-card">
      <div className="vkpi-card__header">
        <h2>AI 周报总结 <span className="vkpi-ai-badge">AI</span></h2>
      </div>
      <p className="vkpi-summary-text">{summary}</p>
      <button className="vkpi-button" type="button">查看完整总结</button>
    </section>
  );
}
