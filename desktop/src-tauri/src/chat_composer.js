(() => {
  let state = null;
  const render = () => {
    const card = document.querySelector('[data-composer-card]');
    const target = card?.querySelector('[class$="_modes"]') || card?.querySelector('[class$="_tools"]');
    if (!target) return;
    const english = (state?.language || document.documentElement.lang || navigator.language).toLowerCase().startsWith('en');
    const modeLabel = english ? 'Reasoning mode' : '推理模式';
    let label = target.querySelector('[data-omaax-modes]');
    if (!state?.sessionId || (!state.levels?.length && !state.error)) { label?.remove(); return; }
    if (!label) {
      label = document.createElement('label'); label.dataset.omaaxModes = '1';
      label.style.cssText = 'display:inline-flex;align-items:center;gap:6px;max-width:100%;font-size:13px;color:inherit;padding:4px 8px;';
      const caption = document.createElement('span'); caption.textContent = modeLabel;
      const select = document.createElement('select'); select.setAttribute('aria-label', modeLabel);
      select.style.cssText = 'font:inherit;color:inherit;background:transparent;border:0;max-width:80px;cursor:pointer;';
      select.addEventListener('change', event => {
        event.stopPropagation();
        if (!state || state.busy) return;
        const sid = state.sessionId, effort = select.value;
        select.disabled = true;
        // Use an HTTP navigation intercepted by the native view; WebView2 may
        // dispatch unknown URI schemes to Windows before its navigation handler.
        window.location.href = location.origin + '/__omaax_mode?session=' + encodeURIComponent(sid) + '&effort=' + encodeURIComponent(effort);
      });
      label.addEventListener('click', event => event.stopPropagation());
      label.append(caption, select); target.append(label);
    }
    const select = label.querySelector('select');
    const levels = (state.levels || []).filter(v => ['low','high','max'].includes(v));
    const signature = (english ? 'en:' : 'zh:') + levels.join(',');
    if (select.dataset.levels !== signature) {
      select.replaceChildren(...levels.map(value => { const o = document.createElement('option'); o.value = value; o.textContent = (english ? {low:'Low',high:'Medium',max:'High'} : {low:'低',high:'中',max:'高'})[value]; return o; }));
      select.dataset.levels = signature;
    }
    select.value = state.selected || ''; select.disabled = !!state.busy || !levels.length;
    select.setAttribute('aria-label', modeLabel);
    label.title = state.error || '';
    const caption = label.querySelector('span'), text = state.error || modeLabel;
    if (caption.textContent !== text) caption.textContent = text;
  };
  window.addEventListener('omaax-reasoning', event => { state = event.detail; render(); });
  const start = () => {
    const style = document.createElement('style');
    style.textContent = '[data-chat-flow-kind="system-prompt"],[data-chat-flow-kind="trajectory-system-message"]{display:none!important} [data-composer-card] [class$="_tools"],[data-composer-card] [class$="_modes"]{flex-wrap:wrap}';
    document.head.append(style);
    let pending = false;
    new MutationObserver(() => { if (pending) return; pending = true; requestAnimationFrame(() => { pending = false; render(); }); }).observe(document.body,{childList:true,subtree:true});
    render();
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
