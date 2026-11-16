return (async () => {
  const form = typeof context.panel.formValue === 'function'
    ? context.panel.formValue()
    : context.panel.formValue;
  const value = Number(form.setpoint_c);
  const api = context.grafana.templateService.replace('${control_api_url}').replace(/\/$/, '');
  if (!Number.isFinite(value) || value < 25 || value > 36) {
    context.grafana.notifyError(['Setpoint', 'Enter a temperature between 25 and 36 °C.']);
    return;
  }
  let endpoint;
  try { endpoint = new URL(api); } catch (_) { endpoint = null; }
  if (!endpoint || endpoint.protocol !== 'https:' || endpoint.hostname.endsWith('.invalid') ||
      endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    context.grafana.notifyError(['Configuration required', 'Set control_api_url to the HTTPS address of the control API.']);
    return;
  }
  context.panel.disableSubmit();
  context.panel.patchFormValue({status: 'Waiting for confirmation from the Pi...'});
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 15000);
  try {
    const response = await fetch(api + '/api/setpoint', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({setpoint_c: Math.round(value * 100) / 100}),
      signal: abort.signal,
      credentials: 'omit',
      cache: 'no-store'
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'applied') {
      const reason = {
        400: 'Invalid setpoint.',
        403: 'The Grafana origin is not allowed by the control API.',
        409: 'A command is already in progress. Wait for confirmation before trying again.',
        429: 'Wait at least one second between commands.',
        502: 'The API did not receive a valid response from IoT Hub or the Pi.',
        503: 'The Pi is offline or remote setpoint mode is not enabled.',
        504: 'Confirmation is missing. Check the active setpoint in telemetry before retrying.'
      }[response.status] || 'The command was not confirmed.';
      throw new Error(reason);
    }
    const message = `${data.device_id}: ${Number(data.setpoint_c).toFixed(2)} °C applied`;
    context.panel.patchFormValue({status: message});
    context.grafana.notifySuccess(['Setpoint confirmed', message]);
    context.grafana.refresh();
  } catch (error) {
    const message = error.name === 'AbortError'
      ? 'Request timed out. Check the active setpoint in telemetry before retrying.'
      : (error.message === 'Failed to fetch'
        ? 'Cannot reach the control API. Check HTTPS, network access and the allowed Grafana origin.'
        : error.message);
    context.panel.patchFormValue({status: message});
    context.grafana.notifyError(['Setpoint not confirmed', message]);
  } finally {
    clearTimeout(timer);
    context.panel.enableSubmit();
  }
})();
