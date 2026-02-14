/* SuperCoach AI Dashboard — Vanilla JS Application */

// API base URL — connects to local server when running `sc web`
// Auto-detects: if served from GitHub Pages, connect to localhost; if served locally, use same origin
const API_BASE = window.location.hostname.includes('github.io')
    ? 'http://127.0.0.1:8000'
    : '';

const App = {
    state: {
        config: null,
        currentSection: 'team',
        connected: false,
    },

    async init() {
        this.checkConnection();
        // Check connection periodically
        setInterval(() => this.checkConnection(), 15000);
    },

    async checkConnection() {
        const indicator = document.getElementById('connection-status');
        const dot = document.getElementById('connection-dot');
        const text = document.getElementById('connection-text');
        const overlay = document.getElementById('connection-overlay');

        try {
            const res = await fetch(`${API_BASE}/api/config`, {signal: AbortSignal.timeout(3000)});
            this.state.config = await res.json();
            this.state.connected = true;

            const c = this.state.config;
            document.getElementById('sidebar-info').textContent =
                `${c.season} | Round ${c.current_round}`;
            document.getElementById('header-info').textContent =
                `Round ${c.current_round} | ${c.season} | ${c.trades_remaining} trades left`;

            dot.className = 'dot connected';
            text.textContent = 'Connected';
            indicator.title = `Connected to API at ${API_BASE || 'localhost'}`;
            overlay.style.display = 'none';

            // Load team on first successful connection
            if (!this._initialLoad) {
                this._initialLoad = true;
                this.Team.loadTeam();
            }
        } catch (e) {
            this.state.connected = false;
            dot.className = 'dot disconnected';
            text.textContent = 'Disconnected';
            indicator.title = 'API server not running';
            overlay.style.display = 'flex';
        }
    },

    showSection(name) {
        if (!this.state.connected) return;

        // Update nav
        document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
        document.querySelector(`.nav-item[data-section="${name}"]`).classList.add('active');

        // Update sections
        document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
        document.getElementById(`section-${name}`).classList.add('active');

        this.state.currentSection = name;

        // Load data when switching sections
        if (name === 'dashboard') this.Dashboard.loadAll();
    },

    // --- Team Builder ---
    Team: {
        _searchTimeout: null,
        _openDropdown: null,

        debounceSearch(query) {
            clearTimeout(this._searchTimeout);
            this._searchTimeout = setTimeout(() => this.searchPlayers(query), 300);
        },

        async searchPlayers(query) {
            const container = document.getElementById('search-results');
            if (!query || query.length < 2) {
                container.innerHTML = '<div class="empty-state">Type at least 2 characters to search</div>';
                return;
            }

            try {
                const res = await fetch(`${API_BASE}/api/players/search?q=${encodeURIComponent(query)}&limit=30`);
                const data = await res.json();

                if (!data.players.length) {
                    container.innerHTML = '<div class="empty-state">No players found</div>';
                    return;
                }

                let html = '<table class="data-table"><thead><tr>';
                html += '<th>Player</th><th>Team</th><th>Pos</th><th class="right">Salary</th><th class="right">Avg</th><th></th>';
                html += '</tr></thead><tbody>';

                for (const p of data.players) {
                    const salaryStr = p.salary ? `$${p.salary.toLocaleString()}` : '-';
                    const avgStr = p.sc_avg ? p.sc_avg.toFixed(0) : '-';
                    const btnHtml = p.is_on_team
                        ? '<span style="color:var(--accent-green);">&#10003;</span>'
                        : `<div class="slot-picker"><button class="btn btn-sm btn-success" onclick="App.Team.showSlotPicker(${p.id}, '${p.position || ''}', this)">+ Add</button></div>`;

                    html += `<tr>`;
                    html += `<td>${this._esc(p.name)}</td>`;
                    html += `<td class="muted">${this._esc(p.team)}</td>`;
                    html += `<td class="muted">${this._esc(p.position || '-')}</td>`;
                    html += `<td class="right">${salaryStr}</td>`;
                    html += `<td class="right">${avgStr}</td>`;
                    html += `<td class="right">${btnHtml}</td>`;
                    html += `</tr>`;
                }

                html += '</tbody></table>';
                container.innerHTML = html;
            } catch (e) {
                container.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
            }
        },

        showSlotPicker(playerId, position, btn) {
            // Close any open dropdown
            this.closeDropdowns();

            // Determine available slots based on position
            const slotGroups = {
                'DEF': Array.from({length: 6}, (_, i) => `DEF${i+1}`),
                'MID': Array.from({length: 8}, (_, i) => `MID${i+1}`),
                'RUC': ['RUC1', 'RUC2'],
                'FWD': Array.from({length: 6}, (_, i) => `FWD${i+1}`),
            };

            let slots = [];
            // Add position-specific slots first
            const posKey = (position || '').split('/')[0].toUpperCase();
            if (slotGroups[posKey]) {
                slots = [...slotGroups[posKey]];
            }
            // Always add BENCH and FLEX
            slots = slots.concat(
                Array.from({length: 8}, (_, i) => `BENCH${i+1}`),
                ['FLEX1']
            );

            // Build dropdown
            const picker = btn.closest('.slot-picker');
            let html = '<div class="slot-dropdown">';
            for (const s of slots) {
                html += `<div class="slot-option" onclick="App.Team.addPlayer(${playerId}, '${s}')">${s}</div>`;
            }
            html += '</div>';
            picker.insertAdjacentHTML('beforeend', html);

            this._openDropdown = picker.querySelector('.slot-dropdown');

            // Close on outside click
            setTimeout(() => {
                document.addEventListener('click', this._closeHandler = (e) => {
                    if (!picker.contains(e.target)) this.closeDropdowns();
                }, {once: true});
            }, 0);
        },

        closeDropdowns() {
            if (this._openDropdown) {
                this._openDropdown.remove();
                this._openDropdown = null;
            }
        },

        async addPlayer(playerId, slot) {
            this.closeDropdowns();
            try {
                const res = await fetch(`${API_BASE}/api/team/slot`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({player_id: playerId, position_slot: slot}),
                });
                const data = await res.json();
                if (!res.ok) {
                    alert(data.detail || 'Failed to add player');
                    return;
                }
                this.loadTeam();
                // Refresh search to update "on team" flags
                const q = document.getElementById('player-search').value;
                if (q) this.searchPlayers(q);
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        async removePlayer(slotId) {
            try {
                await fetch(`${API_BASE}/api/team/slot/${slotId}`, {method: 'DELETE'});
                this.loadTeam();
                const q = document.getElementById('player-search').value;
                if (q) this.searchPlayers(q);
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        async setCaptain(playerId) {
            try {
                await fetch(`${API_BASE}/api/team/captain`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({captain_id: playerId}),
                });
                this.loadTeam();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        async setVC(playerId) {
            // Find current captain
            const res = await fetch(`${API_BASE}/api/team`);
            const data = await res.json();
            const captain = data.slots.find(s => s.is_captain);
            if (!captain) {
                alert('Set a captain first');
                return;
            }
            try {
                await fetch(`${API_BASE}/api/team/captain`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({captain_id: captain.player_id, vice_captain_id: playerId}),
                });
                this.loadTeam();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        async clearTeam() {
            if (!confirm('Clear your entire team?')) return;
            try {
                await fetch(`${API_BASE}/api/team/clear`, {method: 'POST'});
                this.loadTeam();
                const q = document.getElementById('player-search').value;
                if (q) this.searchPlayers(q);
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        importCSV() {
            document.getElementById('csv-upload').click();
        },

        async handleCSV(event) {
            const file = event.target.files[0];
            if (!file) return;
            const form = new FormData();
            form.append('file', file);
            try {
                const res = await fetch(`${API_BASE}/api/team/import-csv`, {method: 'POST', body: form});
                const data = await res.json();
                if (data.success) {
                    alert(`Imported ${data.imported} players`);
                    this.loadTeam();
                } else {
                    alert('Import failed: ' + (data.detail || 'Unknown error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
            event.target.value = '';
        },

        async loadTeam() {
            try {
                const res = await fetch(`${API_BASE}/api/team`);
                const data = await res.json();
                this.renderTeam(data);
            } catch (e) {
                console.error('Failed to load team:', e);
            }
        },

        renderTeam(data) {
            const container = document.getElementById('team-display');
            document.getElementById('team-count').textContent = data.player_count;
            document.getElementById('salary-total').textContent = data.salary_total
                ? `$${data.salary_total.toLocaleString()}`
                : '$0';

            if (!data.slots.length) {
                container.innerHTML = '<div class="empty-state">No team loaded yet. Search and add players, or import a CSV.</div>';
                return;
            }

            // Group slots by position prefix
            const groups = {DEF: [], MID: [], RUC: [], FWD: [], BENCH: [], FLEX: []};
            for (const s of data.slots) {
                const prefix = s.position_slot.replace(/\d+$/, '');
                if (groups[prefix]) groups[prefix].push(s);
                else groups['BENCH'].push(s);
            }

            let html = '';
            for (const [group, slots] of Object.entries(groups)) {
                if (!slots.length) continue;
                html += `<div class="team-group">`;
                html += `<div class="team-group-header">${group} (${slots.length})</div>`;
                for (const s of slots) {
                    const badges = [];
                    if (s.is_captain) badges.push('<span class="badge badge-captain">C</span>');
                    if (s.is_vice_captain) badges.push('<span class="badge badge-vc">VC</span>');
                    if (s.is_emergency) badges.push('<span class="badge badge-emg">EMG</span>');

                    const salary = s.salary ? `$${s.salary.toLocaleString()}` : '';

                    html += `<div class="team-slot">`;
                    html += `<span class="slot-label">${s.position_slot}</span>`;
                    html += `<span class="player-name">${this._esc(s.player_name)}${badges.join('')}</span>`;
                    html += `<span class="player-team">${this._esc(s.team)}</span>`;
                    html += `<span class="player-salary">${salary}</span>`;
                    html += `<div class="actions">`;
                    html += `<button class="btn btn-sm" onclick="App.Team.setCaptain(${s.player_id})" title="Set Captain">C</button>`;
                    html += `<button class="btn btn-sm" onclick="App.Team.setVC(${s.player_id})" title="Set Vice Captain">VC</button>`;
                    html += `<button class="btn btn-sm btn-danger" onclick="App.Team.removePlayer(${s.id})" title="Remove">x</button>`;
                    html += `</div>`;
                    html += `</div>`;
                }
                html += `</div>`;
            }

            container.innerHTML = html;
        },

        _esc(str) {
            const div = document.createElement('div');
            div.textContent = str || '';
            return div.innerHTML;
        },
    },

    // --- Dashboard ---
    Dashboard: {
        async loadAll() {
            const round = App.state.config ? App.state.config.current_round : 1;

            // Fetch all analytics in parallel
            const [projRes, captRes, tradeRes, injRes] = await Promise.allSettled([
                fetch(`${API_BASE}/api/analytics/projections?round=${round}&team_only=true`).then(r => r.json()),
                fetch(`${API_BASE}/api/analytics/captain?round=${round}&top_n=10`).then(r => r.json()),
                fetch(`${API_BASE}/api/analytics/trades?round=${round}`).then(r => r.json()),
                fetch(`${API_BASE}/api/analytics/injuries`).then(r => r.json()),
            ]);

            // Update stat cards
            document.getElementById('stat-round').textContent = round;
            if (App.state.config) {
                document.getElementById('stat-trades').textContent = App.state.config.trades_remaining;
            }

            if (projRes.status === 'fulfilled') this.renderProjections(projRes.value);
            if (captRes.status === 'fulfilled') this.renderCaptain(captRes.value);
            if (tradeRes.status === 'fulfilled') this.renderTrades(tradeRes.value);
            if (injRes.status === 'fulfilled') this.renderInjuries(injRes.value);
        },

        renderProjections(data) {
            document.getElementById('stat-projected').textContent =
                data.total_projected ? data.total_projected.toFixed(0) : '-';

            const container = document.getElementById('projections-table');
            if (!data.projections || !data.projections.length) {
                container.innerHTML = '<div class="empty-state">No projections available. Import your team and data first.</div>';
                return;
            }

            let html = '<table class="data-table"><thead><tr>';
            html += '<th>#</th><th>Player</th><th>Team</th><th>Pos</th>';
            html += '<th class="right">Projected</th><th class="right">Floor</th>';
            html += '<th class="right">Ceiling</th><th class="right">DVP</th>';
            html += '<th class="right">Conf</th><th>Opponent</th>';
            html += '</tr></thead><tbody>';

            data.projections.forEach((p, i) => {
                const confClass = p.confidence >= 0.6 ? 'green' : p.confidence >= 0.3 ? '' : 'red';
                html += `<tr>`;
                html += `<td class="muted">${i + 1}</td>`;
                html += `<td>${esc(p.player_name)}</td>`;
                html += `<td class="muted">${esc(p.team)}</td>`;
                html += `<td class="muted">${esc(p.position || '-')}</td>`;
                html += `<td class="right" style="font-weight:700">${p.projected_score.toFixed(0)}</td>`;
                html += `<td class="right muted">${p.floor.toFixed(0)}</td>`;
                html += `<td class="right muted">${p.ceiling.toFixed(0)}</td>`;
                html += `<td class="right">${p.dvp_adjustment ? (p.dvp_adjustment > 0 ? '+' : '') + p.dvp_adjustment.toFixed(0) : '-'}</td>`;
                html += `<td class="right" style="color:var(--accent-${confClass || 'text-secondary'})">${(p.confidence * 100).toFixed(0)}%</td>`;
                html += `<td class="muted">${esc(p.opponent || '-')}</td>`;
                html += `</tr>`;
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        },

        renderCaptain(data) {
            if (data.candidates && data.candidates.length) {
                document.getElementById('stat-captain').textContent = data.candidates[0].player_name.split(' ').pop();
            }

            const container = document.getElementById('captain-table');
            if (!data.candidates || !data.candidates.length) {
                container.innerHTML = '<div class="empty-state">No captain data. Import your team first.</div>';
                return;
            }

            let html = '<table class="data-table"><thead><tr>';
            html += '<th>#</th><th>Player</th><th>Team</th><th>Pos</th>';
            html += '<th class="right">Proj</th><th class="right">Floor</th>';
            html += '<th class="right">Ceiling</th><th class="right">Consistency</th>';
            html += '<th class="right">Score</th><th>Opponent</th><th class="right">DVP</th>';
            html += '</tr></thead><tbody>';

            data.candidates.forEach((c, i) => {
                const style = i === 0 ? 'color:var(--accent-green);font-weight:700' : i === 1 ? 'color:var(--accent-cyan)' : '';
                html += `<tr style="${style}">`;
                html += `<td>${i + 1}</td>`;
                html += `<td>${esc(c.player_name)}</td>`;
                html += `<td class="muted">${esc(c.team)}</td>`;
                html += `<td class="muted">${esc(c.position || '-')}</td>`;
                html += `<td class="right">${c.projected_score.toFixed(0)}</td>`;
                html += `<td class="right">${c.floor.toFixed(0)}</td>`;
                html += `<td class="right">${c.ceiling.toFixed(0)}</td>`;
                html += `<td class="right">${(c.consistency * 100).toFixed(0)}%</td>`;
                html += `<td class="right" style="font-weight:700">${c.captain_score.toFixed(0)}</td>`;
                html += `<td class="muted">${esc(c.opponent || '-')}</td>`;
                html += `<td class="right">${c.dvp_rank || '-'}</td>`;
                html += `</tr>`;
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        },

        renderTrades(data) {
            const container = document.getElementById('trades-display');
            if (!data.recommendations || !data.recommendations.length) {
                container.innerHTML = '<div class="empty-state">No trades recommended -- your team looks solid!</div>';
                return;
            }

            let html = '';
            data.recommendations.forEach((rec, i) => {
                const out = rec.trade_out;
                const inp = rec.trade_in;
                html += `<div class="trade-card">`;
                html += `<div class="trade-row">`;
                html += `<span class="trade-label out">OUT</span>`;
                html += `<span class="trade-detail">${esc(out.player_name)} (${esc(out.team)}) -- $${out.current_price.toLocaleString()}</span>`;
                html += `</div>`;
                html += `<div style="padding-left:52px;font-size:12px;color:var(--text-secondary);margin-bottom:8px">${esc(out.reason)}</div>`;
                html += `<div class="trade-row">`;
                html += `<span class="trade-label in">IN</span>`;
                html += `<span class="trade-detail">${esc(inp.player_name)} (${esc(inp.team)}) -- $${inp.current_price.toLocaleString()} | Proj: ${inp.projected_score.toFixed(0)}</span>`;
                html += `</div>`;
                html += `<div class="trade-stats">Net: $${rec.net_price.toLocaleString()} | Projected gain: +${rec.projected_gain.toFixed(0)} pts</div>`;
                html += `</div>`;
            });

            container.innerHTML = html;
        },

        renderInjuries(data) {
            document.getElementById('stat-injuries').textContent = data.team_injuries.length;

            const container = document.getElementById('injuries-display');
            if (!data.team_injuries.length) {
                container.innerHTML = '<div class="empty-state" style="color:var(--accent-green)">No injuries on your team!</div>';
                return;
            }

            let html = '';
            data.team_injuries.forEach(inj => {
                html += `<div class="injury-alert" style="display:block;margin-bottom:8px;padding:10px">`;
                html += `<strong>${esc(inj.player_name)}</strong> (${esc(inj.team)}) -- `;
                html += `${esc(inj.injury_type || 'Unknown')} | Return: ${esc(inj.estimated_return || 'TBC')}`;
                html += `</div>`;
            });
            container.innerHTML = html;
        },
    },

    // --- AI Insights ---
    AI: {
        _cache: {},

        switchTab(tab) {
            document.querySelectorAll('.ai-tab').forEach(el => el.classList.remove('active'));
            document.querySelector(`.ai-tab[data-tab="${tab}"]`).classList.add('active');

            document.querySelectorAll('.ai-panel').forEach(el => el.style.display = 'none');
            document.getElementById(`ai-panel-${tab}`).style.display = 'block';
        },

        async loadWeekly() {
            const container = document.getElementById('ai-weekly-content');
            container.innerHTML = '<div class="loading"><div class="spinner"></div><div>Asking Claude...</div></div>';
            try {
                const res = await fetch(`${API_BASE}/api/ai/weekly`);
                const data = await res.json();
                container.innerHTML = renderMarkdown(data.response);
            } catch (e) {
                container.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
            }
        },

        async loadCaptain() {
            const container = document.getElementById('ai-captain-content');
            const round = App.state.config ? App.state.config.current_round : 1;
            container.innerHTML = '<div class="loading"><div class="spinner"></div><div>Asking Claude...</div></div>';
            try {
                const res = await fetch(`${API_BASE}/api/ai/captain?round=${round}`);
                const data = await res.json();
                container.innerHTML = renderMarkdown(data.response);
            } catch (e) {
                container.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
            }
        },

        async loadTrades() {
            const container = document.getElementById('ai-trades-content');
            const round = App.state.config ? App.state.config.current_round : 1;
            container.innerHTML = '<div class="loading"><div class="spinner"></div><div>Asking Claude...</div></div>';
            try {
                const res = await fetch(`${API_BASE}/api/ai/trades?round=${round}`);
                const data = await res.json();
                container.innerHTML = renderMarkdown(data.response);
            } catch (e) {
                container.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
            }
        },

        async sendChat() {
            const input = document.getElementById('chat-input');
            const msg = input.value.trim();
            if (!msg) return;
            input.value = '';

            const log = document.getElementById('chat-log');
            log.innerHTML += `<div class="chat-msg user">${esc(msg)}</div>`;
            log.innerHTML += `<div class="chat-msg assistant" id="chat-pending"><div class="loading" style="padding:10px"><div class="spinner"></div></div></div>`;
            log.scrollTop = log.scrollHeight;

            try {
                const res = await fetch(`${API_BASE}/api/ai/chat`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message: msg}),
                });
                const data = await res.json();
                const pending = document.getElementById('chat-pending');
                if (pending) {
                    pending.id = '';
                    pending.innerHTML = renderMarkdown(data.response);
                }
            } catch (e) {
                const pending = document.getElementById('chat-pending');
                if (pending) {
                    pending.id = '';
                    pending.innerHTML = `<span style="color:var(--accent-red)">Error: ${e.message}</span>`;
                }
            }
            log.scrollTop = log.scrollHeight;
        },
    },
};

// --- Helpers ---

function esc(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

function renderMarkdown(text) {
    if (!text) return '';
    let html = esc(text);

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // Bold and italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Lists
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/^(\d+)\. (.+)$/gm, '<li>$2</li>');
    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');

    // Line breaks (double newline = paragraph)
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';

    // Clean up empty paragraphs
    html = html.replace(/<p>\s*<\/p>/g, '');
    html = html.replace(/<p>\s*(<h[1-3]>)/g, '$1');
    html = html.replace(/(<\/h[1-3]>)\s*<\/p>/g, '$1');
    html = html.replace(/<p>\s*(<ul>)/g, '$1');
    html = html.replace(/(<\/ul>)\s*<\/p>/g, '$1');

    return html;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', () => App.init());
