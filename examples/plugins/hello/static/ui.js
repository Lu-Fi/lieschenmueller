// ES-Modul: der Kern ruft view(ctx) auf. ctx: {root, $, api, post, esc, badge, fmt, getContacts, openCall}
export async function view(ctx) {
  const s = await ctx.api("/api/plugins/hello/stats");
  ctx.root.innerHTML = `<div class=head><div><h2>Hallo-Beispiel</h2>
    <p class=sub style="margin:0">Dieses Plugin hat bisher ${ctx.esc(s.anrufe_gezaehlt)} Anrufe gezählt.</p></div></div>`;
}
