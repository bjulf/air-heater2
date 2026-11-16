return (async () => {
  const form = typeof context.panel.formValue === 'function'
    ? context.panel.formValue()
    : context.panel.formValue;
  const value = Number(form.setpoint_c);
  if (!Number.isFinite(value) || value < 25 || value > 36) {
    context.grafana.notifyError(['Setpoint', 'Enter a temperature between 25 and 36 °C.']);
    return;
  }
  context.panel.disableSubmit();
  context.panel.patchFormValue({status: 'Waiting for confirmation from the Pi...'});
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 15000);
  try {
    // Use Grafana's same-origin server proxy; Managed Grafana blocks external fetch via CSP.
    const lookup = await fetch('/api/datasources/name/airheater-control-api', {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', signal: abort.signal
    });
    if (!lookup.ok) {
      throw new Error('Create a JSON API data source named airheater-control-api pointing to the VM HTTPS address.');
    }
    const source = await lookup.json();
    if (!source.uid || source.type !== 'marcusolsson-json-datasource' || source.access !== 'proxy') {
      throw new Error('airheater-control-api must be a JSON API data source using Server/Proxy access.');
    }
    const response = await fetch('/api/datasources/proxy/uid/' + encodeURIComponent(source.uid) + '/api/setpoint', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({setpoint_c: Math.round(value * 100) / 100}),
      signal: abort.signal,
      credentials: 'same-origin',
      cache: 'no-store'
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'applied') {
      const reason = {
        400: 'Invalid setpoint.',
        403: 'Grafana access or the API origin policy denied the command.',
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
        ? 'Cannot reach the Grafana data source proxy. Check the browser console and data source configuration.'
        : error.message);
    context.panel.patchFormValue({status: message});
    context.grafana.notifyError(['Setpoint not confirmed', message]);
  } finally {
    clearTimeout(timer);
    context.panel.enableSubmit();
  }
})();
