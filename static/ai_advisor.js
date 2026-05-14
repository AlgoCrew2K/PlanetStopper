async function getSuggestions() {
    const symphonyId = document.getElementById('symphony-id-input').value.trim();
    const errorEl = document.getElementById('advisor-error');
    const container = document.getElementById('suggestions-container');
    const btn = document.getElementById('get-suggestions-btn');

    errorEl.classList.add('hidden');
    errorEl.textContent = '';
    container.innerHTML = '<p class="text-slate-400 text-sm">Loading suggestions...</p>';
    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
        const resp = await fetch('/ai-advisor/suggest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symphony_id: symphonyId }),
        });
        const body = await resp.json();

        if (body.error) {
            container.innerHTML = '';
            errorEl.textContent = 'Advisor unavailable: ' + body.error;
            errorEl.classList.remove('hidden');
            return;
        }

        renderSuggestions(body.suggestions || [], symphonyId);
    } catch (err) {
        container.innerHTML = '';
        errorEl.textContent = 'Request failed: ' + err.message;
        errorEl.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Get Suggestions';
    }
}

function renderSuggestions(suggestions, symphonyId) {
    const container = document.getElementById('suggestions-container');
    if (suggestions.length === 0) {
        container.innerHTML = '<p class="text-slate-400 text-sm bg-slate-800 rounded-xl border border-slate-700 p-6">No suggestions — the current config looks sound.</p>';
        return;
    }

    container.innerHTML = suggestions.map((s, i) => `
        <div id="card-${i}" class="bg-slate-800 rounded-2xl border border-slate-700 p-6">
            <div class="flex items-start justify-between gap-4">
                <div class="flex-1">
                    <div class="flex items-center gap-3 mb-2">
                        <span class="text-[10px] font-bold uppercase tracking-widest text-violet-400">${escHtml(s.config_key)}</span>
                        <span class="text-[10px] text-slate-500">${escHtml(s.risk_direction)} &middot; ${escHtml(s.confidence)} confidence</span>
                    </div>
                    <div class="flex items-center gap-3 mb-3">
                        <span class="text-slate-400 text-sm">Current: <span class="font-mono text-slate-200">${escHtml(String(s.current_value))}</span></span>
                        <span class="text-slate-600">&rarr;</span>
                        <span class="text-slate-400 text-sm">Suggested: <span class="font-mono text-emerald-400">${escHtml(String(s.suggested_value))}</span></span>
                    </div>
                    <div class="relative inline-block">
                        <span class="text-[11px] text-slate-400 underline decoration-dotted cursor-help"
                            title="${escHtml(s.rationale)}">
                            Hover for rationale
                        </span>
                        <p class="text-[11px] text-slate-500 mt-1">${escHtml(s.rationale.substring(0, 120))}${s.rationale.length > 120 ? '…' : ''}</p>
                    </div>
                </div>
                <div class="flex flex-col gap-2">
                    <button onclick="acceptSuggestion(${i}, '${escHtml(symphonyId)}')"
                        class="px-4 py-1.5 bg-emerald-700 hover:bg-emerald-600 text-white text-[11px] font-bold uppercase tracking-widest rounded-lg transition-colors">
                        Accept
                    </button>
                    <button onclick="rejectSuggestion(${i}, '${escHtml(symphonyId)}')"
                        class="px-4 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 text-[11px] font-bold uppercase tracking-widest rounded-lg transition-colors">
                        Reject
                    </button>
                </div>
            </div>
        </div>
    `).join('');

    // Store suggestions on the container for accept/reject handlers
    container._suggestions = suggestions;
    container._symphonyId = symphonyId;
}

async function acceptSuggestion(index, symphonyId) {
    const container = document.getElementById('suggestions-container');
    const suggestion = container._suggestions[index];
    const card = document.getElementById('card-' + index);

    card.classList.add('opacity-50');
    try {
        const resp = await fetch('/ai-advisor/accept', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symphony_id: symphonyId, suggestion }),
        });
        const body = await resp.json();
        if (body.status === 'accepted') {
            card.innerHTML = '<p class="text-emerald-400 text-sm font-bold">Accepted — config updated.</p>';
        } else {
            card.classList.remove('opacity-50');
            alert('Rejected by C2 gate: ' + (body.error || body.status));
        }
    } catch (err) {
        card.classList.remove('opacity-50');
        alert('Request failed: ' + err.message);
    }
}

async function rejectSuggestion(index, symphonyId) {
    const container = document.getElementById('suggestions-container');
    const suggestion = container._suggestions[index];
    const card = document.getElementById('card-' + index);

    card.classList.add('opacity-50');
    try {
        await fetch('/ai-advisor/reject', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symphony_id: symphonyId, suggestion }),
        });
        card.innerHTML = '<p class="text-slate-500 text-sm">Rejected.</p>';
    } catch (err) {
        card.classList.remove('opacity-50');
        alert('Request failed: ' + err.message);
    }
}

function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
