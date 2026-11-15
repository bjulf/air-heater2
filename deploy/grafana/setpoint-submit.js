return (async () => {
  const form = context.panel.formValue();
  const value = Number(form.setpoint_c);
  const token = String(form.operator_token || '').trim();
  const api = context.grafana.templateService.replace('${control_api_url}').replace(/\/$/, '');
  if (!Number.isFinite(value) || value < 25 || value > 36) {
    context.grafana.notifyError(['Settpunkt', 'Velg en temperatur fra 25 til 36 °C.']);
    return;
  }
  let endpoint;
  try { endpoint = new URL(api); } catch (_) { endpoint = null; }
  if (!endpoint || endpoint.protocol !== 'https:' || endpoint.hostname.endsWith('.invalid') ||
      endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    context.grafana.notifyError(['Oppsett mangler', 'Sett control_api_url til API-ets HTTPS-adresse.']);
    return;
  }
  if (token.length < 32) {
    context.grafana.notifyError(['Kontrollnøkkel', 'Skriv inn kontrollnøkkelen fra VM-oppsettet.']);
    return;
  }
  context.panel.disableSubmit();
  context.panel.patchFormValue({status: 'Venter på bekreftelse fra Pi …'});
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 15000);
  try {
    const response = await fetch(api + '/api/setpoint', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token},
      body: JSON.stringify({setpoint_c: Math.round(value * 100) / 100}),
      signal: abort.signal,
      credentials: 'omit',
      cache: 'no-store'
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'applied') {
      const reason = {
        400: 'Ugyldig settpunkt.', 401: 'Kontrollnøkkelen er feil.',
        403: 'Grafana-adressen er ikke tillatt i API-oppsettet.',
        409: 'En kommando behandles allerede. Prøv igjen etter bekreftelse.',
        429: 'Vent minst ett sekund mellom kommandoer.',
        502: 'API-et fikk ikke gyldig svar fra IoT Hub eller Pi.',
        503: 'Pi er ikke tilkoblet eller fjernstyringsmodus er ikke startet.',
        504: 'Bekreftelse mangler. Kontroller aktivt settpunkt i telemetrien før nytt forsøk.'
      }[response.status] || 'Kommandoen ble ikke bekreftet.';
      throw new Error(reason);
    }
    const message = `${data.device_id}: ${Number(data.setpoint_c).toFixed(2)} °C tatt i bruk`;
    context.panel.patchFormValue({status: message});
    context.grafana.notifySuccess(['Settpunkt bekreftet', message]);
    context.grafana.refresh();
  } catch (error) {
    const message = error.name === 'AbortError'
      ? 'Tidsavbrudd: kontroller aktivt settpunkt i telemetrien før nytt forsøk.'
      : (error.message === 'Failed to fetch'
        ? 'Ingen API-forbindelse. Kontroller HTTPS, nettverk og tillatt Grafana-adresse.'
        : error.message);
    context.panel.patchFormValue({status: message});
    context.grafana.notifyError(['Settpunkt ikke bekreftet', message]);
  } finally {
    clearTimeout(timer);
    // Keep this credential out of panel defaults and dashboard exports.
    context.panel.patchFormValue({operator_token: ''});
    context.panel.enableSubmit();
  }
})();
