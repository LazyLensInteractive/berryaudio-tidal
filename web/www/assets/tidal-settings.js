const rpc = async (method, params = null) => {
  const response = await fetch('/rpc', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({jsonrpc: '2.0', id: Date.now(), method, params}),
  });
  const payload = await response.json();
  if (payload.error) throw new Error(payload.error.message);
  return payload.result;
};

const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
})[char]);

const render = (status = {}) => {
  const modal = document.querySelector('#tidal-settings-modal');
  modal.querySelector('[data-status]').textContent = status.configured
    ? `Connected (${status.client_id_hint})`
    : 'Not configured';
  modal.querySelector('[name=country_code]').value = status.country_code || 'US';
  modal.querySelector('[data-scopes]').innerHTML = (status.scopes || [])
    .map((scope) => `<span>${escapeHtml(scope)}</span>`).join('');
  modal.querySelector('[data-disconnect]').hidden = !status.configured;
};

const openSettings = async () => {
  const modal = document.querySelector('#tidal-settings-modal');
  modal.showModal();
  const message = modal.querySelector('[data-message]');
  message.textContent = '';
  try { render(await rpc('tidal.status')); }
  catch (error) { message.textContent = error.message; }
};

const initialize = () => {
  document.body.insertAdjacentHTML('beforeend', `
    <button id="tidal-settings-button" type="button" aria-label="Configure TIDAL">TIDAL Setup</button>
    <dialog id="tidal-settings-modal">
      <form method="dialog" class="tidal-card">
        <button class="tidal-close" value="cancel" aria-label="Close">×</button>
        <h2>TIDAL integration</h2>
        <p class="tidal-subtitle">Credentials stay on this device and are never sent to the browser again.</p>
        <p class="tidal-state"><strong data-status>Loading…</strong></p>
        <label>Client ID<input name="client_id" autocomplete="off" required></label>
        <label>Client Secret<input name="client_secret" type="password" autocomplete="new-password" required></label>
        <label>Country code<input name="country_code" maxlength="2" value="US" required></label>
        <p class="tidal-label">Allowed scopes</p><div class="tidal-scopes" data-scopes></div>
        <p class="tidal-message" data-message aria-live="polite"></p>
        <div class="tidal-actions">
          <button type="button" class="tidal-danger" data-disconnect hidden>Disconnect</button>
          <button type="submit" class="tidal-save">Save & test</button>
        </div>
      </form>
    </dialog>`);
  const modal = document.querySelector('#tidal-settings-modal');
  document.querySelector('#tidal-settings-button').addEventListener('click', openSettings);
  modal.querySelector('form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const message = form.querySelector('[data-message]');
    const save = form.querySelector('.tidal-save');
    save.disabled = true;
    message.textContent = 'Testing credentials…';
    try {
      const status = await rpc('tidal.configure', {
        client_id: form.client_id.value,
        client_secret: form.client_secret.value,
        country_code: form.country_code.value,
      });
      form.client_secret.value = '';
      render(status);
      message.textContent = 'Connected successfully.';
    } catch (error) { message.textContent = error.message; }
    finally { save.disabled = false; }
  });
  modal.querySelector('[data-disconnect]').addEventListener('click', async () => {
    render(await rpc('tidal.disconnect'));
    modal.querySelector('[data-message]').textContent = 'Credentials removed.';
  });
};

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
else initialize();
