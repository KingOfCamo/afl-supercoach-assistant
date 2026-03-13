/* SuperCoach AI Dashboard — Vanilla JS Application */

// API base URL — connects to local server when running `sc web`
// Auto-detects: if served from GitHub Pages, connect to localhost; if served locally, use same origin
const API_BASE = window.location.hostname.includes('github.io')
    ? 'http://127.0.0.1:8000'
    : '';

// AFL team primary colours (for card left-border accents)
const TEAM_COLORS = {
    'Adelaide':          '#002b5c',
    'Brisbane':          '#7b0039',
    'Carlton':           '#002b5c',
    'Collingwood':       '#111111',
    'Essendon':          '#cc0000',
    'Fremantle':         '#7b2d8b',
    'Geelong':           '#002b5c',
    'Gold Coast':        '#d4a843',
    'GWS':               '#f47920',
    'Hawthorn':          '#4d2004',
    'Melbourne':         '#002b5c',
    'North Melbourne':   '#003ea1',
    'Port Adelaide':     '#008aab',
    'Richmond':          '#ffd200',
    'St Kilda':          '#ed1c24',
    'Sydney':            '#ed171f',
    'West Coast':        '#002b5c',
    'Western Bulldogs':  '#014896',
};

// Full team name -> 3-letter abbreviation
const TEAM_ABBREVS = {
    'Adelaide':          'ADE',
    'Brisbane':          'BRL',
    'Carlton':           'CAR',
    'Collingwood':       'COL',
    'Essendon':          'ESS',
    'Fremantle':         'FRE',
    'Geelong':           'GEE',
    'Gold Coast':        'GCS',
    'GWS':               'GWS',
    'Hawthorn':          'HAW',
    'Melbourne':         'MEL',
    'North Melbourne':   'NTH',
    'Port Adelaide':     'PTA',
    'Richmond':          'RIC',
    'St Kilda':          'STK',
    'Sydney':            'SYD',
    'West Coast':        'WCE',
    'Western Bulldogs':  'WBD',
};

// --- Auth helpers ---

function getToken() {
    return localStorage.getItem('sc_token');
}

function authFetch(url, opts = {}) {
    const token = getToken();
    if (!token) {
        window.location.href = '/login';
        return Promise.reject(new Error('Not authenticated'));
    }
    opts.headers = {
        ...(opts.headers || {}),
        'Authorization': `Bearer ${token}`,
    };
    return fetch(url, opts).then(res => {
        if (res.status === 401) {
            localStorage.removeItem('sc_token');
            localStorage.removeItem('sc_user');
            window.location.href = '/login';
            throw new Error('Session expired');
        }
        return res;
    });
}

function logout() {
    localStorage.removeItem('sc_token');
    localStorage.removeItem('sc_user');
    window.location.href = '/login';
}

const App = {
    state: {
        config: null,
        currentSection: 'team',
        connected: false,
    },

    async init() {
        // Redirect to login if no token
        if (!getToken()) {
            window.location.href = '/login';
            return;
        }
        this.checkConnection();
        setInterval(() => this.checkConnection(), 15000);
    },

    async checkConnection() {
        const indicator = document.getElementById('connection-status');
        const dot = document.getElementById('connection-dot');
        const text = document.getElementById('connection-text');
        const overlay = document.getElementById('connection-overlay');

        try {
            const res = await authFetch(`${API_BASE}/api/config`, {signal: AbortSignal.timeout(3000)});
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

            // Load team + live scores on first successful connection
            if (!this._initialLoad) {
                this._initialLoad = true;
                this.Team.loadTeam();
                this.Team.loadLiveScores();
                this.Team.startLiveRefresh();
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
        _currentView: 'field',
        _lastTeamData: null,
        _pendingSlot: null,
        _contextMenu: null,
        _captainMode: null, // null, 'captain', or 'vc'
        _emergencyMode: false,
        _emergencyPicks: [], // player_ids in order
        _scoreView: 'live', // 'live', 'projected', 'average'
        _liveScoresExpanded: false,
        _liveRefreshInterval: null,
        SALARY_CAP: 10000000,

        switchView(view) {
            this._currentView = view;
            document.querySelectorAll('.team-view-tab').forEach(el => el.classList.remove('active'));
            document.querySelector(`.team-view-tab[data-view="${view}"]`).classList.add('active');
            document.querySelectorAll('.team-view').forEach(el => el.classList.remove('active'));
            document.getElementById(`${view}-view`).classList.add('active');
        },

        debounceSearch(query) {
            clearTimeout(this._searchTimeout);
            this._searchTimeout = setTimeout(() => this.searchPlayers(query), 300);
        },

        async searchPlayers(query) {
            const container = document.getElementById('search-results');
            if (!query || query.length < 2) {
                let hint = '<div class="empty-state">Type at least 2 characters to search</div>';
                if (this._pendingSlot) {
                    hint = `<div class="pending-slot-banner">
                        Adding to <strong>${this._pendingSlot}</strong>
                        <button onclick="App.Team.clearPendingSlot()" title="Cancel">&times;</button>
                    </div>` + hint;
                }
                container.innerHTML = hint;
                return;
            }

            // Calculate remaining salary budget
            const salaryUsed = (this._lastTeamData && this._lastTeamData.salary_total) || 0;
            const remaining = this.SALARY_CAP - salaryUsed;

            try {
                const res = await authFetch(`${API_BASE}/api/players/search?q=${encodeURIComponent(query)}&limit=30`);
                const data = await res.json();

                if (!data.players.length) {
                    container.innerHTML = '<div class="empty-state">No players found</div>';
                    return;
                }

                let html = '';

                // Show pending slot banner if active
                if (this._pendingSlot) {
                    html += `<div class="pending-slot-banner">
                        Adding to <strong>${this._pendingSlot}</strong>
                        <button onclick="App.Team.clearPendingSlot()" title="Cancel">&times;</button>
                    </div>`;
                }

                html += '<table class="data-table"><thead><tr>';
                html += '<th>Player</th><th>Team</th><th>Pos</th><th class="right">Salary</th><th class="right">Avg</th><th></th>';
                html += '</tr></thead><tbody>';

                for (const p of data.players) {
                    // Salary with affordability highlighting
                    const affordable = !p.salary || p.salary <= remaining;
                    const salaryClass = p.salary ? (affordable ? 'salary-affordable' : 'salary-over') : '';
                    const salaryStr = p.salary ? `$${p.salary.toLocaleString()}` : '-';
                    const avgStr = p.sc_avg ? p.sc_avg.toFixed(0) : '-';

                    let btnHtml;
                    if (p.is_on_team) {
                        btnHtml = '<span style="color:var(--accent-green);">&#10003;</span>';
                    } else if (this._pendingSlot) {
                        // Direct add to pending slot
                        btnHtml = `<button class="btn btn-sm btn-success" onclick="App.Team.addPlayer(${p.id}, '${this._pendingSlot}')">+ ${this._pendingSlot}</button>`;
                    } else {
                        // Auto-assign with dropdown fallback
                        const autoSlot = this.autoAssignSlot(p.position || '');
                        const autoLabel = autoSlot ? autoSlot : 'Full';
                        btnHtml = `<div class="slot-picker" style="display:flex;gap:2px;">`;
                        if (autoSlot) {
                            btnHtml += `<button class="btn btn-sm btn-success" onclick="App.Team.addPlayer(${p.id}, '${autoSlot}')" title="Auto: ${autoSlot}">+ Add</button>`;
                        }
                        btnHtml += `<button class="btn btn-sm" onclick="App.Team.showSlotPicker(${p.id}, '${p.position || ''}', this)" title="Pick slot" style="padding:2px 5px;">&#9660;</button>`;
                        btnHtml += `</div>`;
                    }

                    html += `<tr>`;
                    html += `<td>${this._esc(p.name)}</td>`;
                    html += `<td class="muted">${this._esc(p.team)}</td>`;
                    html += `<td class="muted">${this._esc(p.position || '-')}</td>`;
                    html += `<td class="right ${salaryClass}">${salaryStr}</td>`;
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

        // --- Auto-assign: find first empty slot matching position ---
        autoAssignSlot(position) {
            const occupied = new Set();
            if (this._lastTeamData && this._lastTeamData.slots) {
                for (const s of this._lastTeamData.slots) occupied.add(s.position_slot);
            }

            const slotGroups = {
                'DEF': Array.from({length: 6}, (_, i) => `DEF${i+1}`),
                'MID': Array.from({length: 8}, (_, i) => `MID${i+1}`),
                'RUC': ['RUC1', 'RUC2'],
                'FWD': Array.from({length: 6}, (_, i) => `FWD${i+1}`),
            };

            // Try each position the player can play (e.g. "DEF/MID")
            const posKeys = (position || '').split('/').map(p => p.trim().toUpperCase());
            for (const posKey of posKeys) {
                if (slotGroups[posKey]) {
                    for (const slot of slotGroups[posKey]) {
                        if (!occupied.has(slot)) return slot;
                    }
                }
            }

            // Fall back to BENCH
            for (let i = 1; i <= 8; i++) {
                if (!occupied.has(`BENCH${i}`)) return `BENCH${i}`;
            }

            // Fall back to FLEX
            if (!occupied.has('FLEX1')) return 'FLEX1';

            return null; // Team is full
        },

        // --- Pending slot (clicked from empty card on field view) ---
        setPendingSlot(slot) {
            this._pendingSlot = slot;
            const searchInput = document.getElementById('player-search');
            searchInput.focus();
            searchInput.value = '';
            // Show pending banner
            const container = document.getElementById('search-results');
            container.innerHTML = `<div class="pending-slot-banner">
                Adding to <strong>${slot}</strong>
                <button onclick="App.Team.clearPendingSlot()" title="Cancel">&times;</button>
            </div>
            <div class="empty-state">Type a player name to search</div>`;
        },

        clearPendingSlot() {
            this._pendingSlot = null;
            const container = document.getElementById('search-results');
            container.innerHTML = '<div class="empty-state">Type a player name to search</div>';
        },

        // --- Context menu on card right-click ---
        showCardMenu(event, slotId, playerId) {
            event.preventDefault();
            event.stopPropagation();
            this.closeCardMenu();

            const slot = this._lastTeamData
                ? this._lastTeamData.slots.find(s => s.id === slotId)
                : null;
            const isBench = slot && slot.position_slot.startsWith('BENCH');
            const playerName = slot ? slot.player_name : '';
            const isCaptain = slot && slot.is_captain;
            const isVC = slot && slot.is_vice_captain;

            let html = '<div class="fc-context-menu">';
            html += `<div class="ctx-header">${esc(playerName)}</div>`;

            // Captain / VC section
            if (!isCaptain) {
                html += `<div class="fc-context-item" onclick="App.Team.setCaptain(${playerId})">&#128081; Set Captain</div>`;
            }
            if (!isVC) {
                html += `<div class="fc-context-item" onclick="App.Team.setVC(${playerId})">&#127775; Set Vice Captain</div>`;
            }

            // Emergency section (bench only)
            if (isBench) {
                html += '<div class="fc-context-sep"></div>';
                const isEmg = slot && slot.is_emergency;
                if (isEmg) {
                    html += `<div class="fc-context-item" onclick="App.Team.quickRemoveEmergency(${playerId})">&#10006; Remove Emergency</div>`;
                } else {
                    html += `<div class="fc-context-item" onclick="App.Team.quickAddEmergency(${playerId})">&#127919; Set Emergency</div>`;
                }
            }

            // Remove
            html += '<div class="fc-context-sep"></div>';
            html += `<div class="fc-context-item danger" onclick="App.Team.removePlayer(${slotId})">&#10060; Remove from team</div>`;
            html += '</div>';

            // Append to body with fixed positioning
            document.body.insertAdjacentHTML('beforeend', html);
            this._contextMenu = document.body.querySelector('.fc-context-menu:last-child');

            // Position near click, but keep on screen
            const menu = this._contextMenu;
            const mx = event.clientX;
            const my = event.clientY;
            const mw = menu.offsetWidth;
            const mh = menu.offsetHeight;
            const vw = window.innerWidth;
            const vh = window.innerHeight;

            let left = mx;
            let top = my;
            // Flip left if overflowing right
            if (mx + mw > vw - 8) left = mx - mw;
            // Flip up if overflowing bottom
            if (my + mh > vh - 8) top = my - mh;
            // Clamp
            if (left < 4) left = 4;
            if (top < 4) top = 4;

            menu.style.left = left + 'px';
            menu.style.top = top + 'px';

            setTimeout(() => {
                document.addEventListener('click', this._contextCloseHandler = () => {
                    this.closeCardMenu();
                }, {once: true});
            }, 0);
        },

        closeCardMenu() {
            if (this._contextMenu) {
                this._contextMenu.remove();
                this._contextMenu = null;
            }
        },

        async addPlayer(playerId, slot) {
            this.closeDropdowns();
            if (!slot) {
                alert('No available slot for this player');
                return;
            }
            try {
                const res = await authFetch(`${API_BASE}/api/team/slot`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({player_id: playerId, position_slot: slot}),
                });
                const data = await res.json();
                if (!res.ok) {
                    alert(data.detail || 'Failed to add player');
                    return;
                }
                this._pendingSlot = null;
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
                await authFetch(`${API_BASE}/api/team/slot/${slotId}`, {method: 'DELETE'});
                this.loadTeam();
                const q = document.getElementById('player-search').value;
                if (q) this.searchPlayers(q);
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        async setCaptain(playerId) {
            try {
                await authFetch(`${API_BASE}/api/team/captain`, {
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
            const res = await authFetch(`${API_BASE}/api/team`);
            const data = await res.json();
            const captain = data.slots.find(s => s.is_captain);
            if (!captain) {
                alert('Set a captain first');
                return;
            }
            try {
                await authFetch(`${API_BASE}/api/team/captain`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({captain_id: captain.player_id, vice_captain_id: playerId}),
                });
                this.loadTeam();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        // --- Captain/VC picker mode ---
        toggleCaptainMode(mode) {
            const captainBtn = document.getElementById('captain-btn');
            const vcBtn = document.getElementById('vc-btn');
            const hint = document.getElementById('captain-hint');

            if (this._captainMode === mode) {
                // Toggle off
                this._captainMode = null;
                captainBtn.classList.remove('selecting');
                vcBtn.classList.remove('selecting');
                hint.style.display = 'none';
            } else {
                this._captainMode = mode;
                captainBtn.classList.toggle('selecting', mode === 'captain');
                vcBtn.classList.toggle('selecting', mode === 'vc');
                hint.style.display = '';
            }
            // Re-render to add/remove clickable indicators
            if (this._lastTeamData) this.renderTeam(this._lastTeamData);
        },

        handleCardClick(playerId) {
            if (!this._captainMode) return;
            if (this._captainMode === 'captain') {
                this.setCaptain(playerId);
            } else {
                this.setVC(playerId);
            }
            // Exit captain mode after selection
            this._captainMode = null;
            document.getElementById('captain-btn').classList.remove('selecting');
            document.getElementById('vc-btn').classList.remove('selecting');
            document.getElementById('captain-hint').style.display = 'none';
        },

        _updateCaptainPicker(data) {
            const captainSlot = data.slots.find(s => s.is_captain);
            const vcSlot = data.slots.find(s => s.is_vice_captain);
            const captainBtn = document.getElementById('captain-btn');
            const vcBtn = document.getElementById('vc-btn');
            const captainName = document.getElementById('captain-name');
            const vcName = document.getElementById('vc-name');

            if (captainSlot) {
                captainName.textContent = captainSlot.player_name;
                captainBtn.classList.add('has-player');
            } else {
                captainName.textContent = 'Select Captain';
                captainBtn.classList.remove('has-player');
            }

            if (vcSlot) {
                vcName.textContent = vcSlot.player_name;
                vcBtn.classList.add('has-player');
            } else {
                vcName.textContent = 'Select Vice Captain';
                vcBtn.classList.remove('has-player');
            }
        },

        // --- Score View Toggle ---
        setScoreView(view) {
            this._scoreView = view;
            document.querySelectorAll('.svt-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.view === view);
            });
            // Re-render cards with new score view
            if (this._lastTeamData) this.renderTeam(this._lastTeamData);
        },

        _getDisplayScore(s) {
            const live = this._liveData;
            if (this._scoreView === 'live' && live && live.players) {
                const lp = live.players.find(p => p.player_id === s.player_id);
                if (lp && lp.live_score != null) return lp.live_score;
                if (lp && lp.projected_final != null) return '~' + Math.round(lp.projected_final);
            }
            if (this._scoreView === 'projected') {
                if (s.projected_score != null) return s.projected_score;
                if (s.sc_avg != null) return '~' + s.sc_avg;
                return '-';
            }
            if (this._scoreView === 'average') {
                return s.season_avg != null ? s.season_avg : (s.sc_avg != null ? s.sc_avg : '-');
            }
            // Default: last score
            return s.last_score != null ? s.last_score : (s.sc_avg != null ? s.sc_avg : 0);
        },

        _getLineupStatus(s) {
            // Check injury
            if (s.injury) {
                return { status: 'injured', label: '!', tooltip: `${s.injury.type || 'Injured'} — ${s.injury.return || 'TBD'}` };
            }
            // Check live data for match status
            if (this._liveData && this._liveData.players) {
                const lp = this._liveData.players.find(p => p.player_id === s.player_id);
                if (lp) {
                    if (lp.match_status === 'complete' && lp.live_score != null) {
                        return { status: 'playing', label: '✓', tooltip: 'Played' };
                    }
                    if (lp.match_status === 'in_progress' && lp.live_score != null) {
                        return { status: 'playing', label: '✓', tooltip: 'Playing now' };
                    }
                    if (lp.match_status === 'complete' && lp.live_score == null) {
                        return { status: 'not-playing', label: '', tooltip: 'Did not play' };
                    }
                }
            }
            return null; // Upcoming or unknown
        },

        // --- Live Scores ---
        _liveData: null,

        async loadLiveScores() {
            const round = App.state.config ? App.state.config.current_round : 1;
            try {
                const res = await authFetch(`${API_BASE}/api/analytics/live?round=${round}`);
                const data = await res.json();
                this._liveData = data;
                this._renderLiveScores(data);
                this._overlayLiveScoresOnCards(data);
            } catch (e) {
                console.error('Failed to load live scores:', e);
            }
        },

        _overlayLiveScoresOnCards(data) {
            // Overlay live scores onto field & bench cards
            if (!data.players) return;
            const scoreMap = {};
            for (const p of data.players) {
                scoreMap[p.player_id] = p;
            }

            // Update field cards
            document.querySelectorAll('.field-card[data-pid]').forEach(card => {
                const pid = parseInt(card.dataset.pid);
                const p = scoreMap[pid];
                if (!p) return;
                let overlay = card.querySelector('.fc-live-overlay');
                if (!overlay) {
                    overlay = document.createElement('div');
                    overlay.className = 'fc-live-overlay';
                    card.appendChild(overlay);
                }
                if (p.live_score != null) {
                    const cls = p.match_status === 'complete' ? 'complete' :
                        p.match_status === 'in_progress' ? 'live' : '';
                    const display = p.is_captain ? p.live_score * 2 : p.live_score;
                    overlay.innerHTML = `<span class="fc-live-score ${cls}">${display}</span>`;
                    overlay.style.display = '';
                } else if (p.match_status === 'upcoming' && p.projected_final != null) {
                    overlay.innerHTML = `<span class="fc-live-score proj">~${Math.round(p.projected_final)}</span>`;
                    overlay.style.display = '';
                } else {
                    overlay.style.display = 'none';
                }
            });

            // Update bench cards
            document.querySelectorAll('.bench-card[data-pid]').forEach(card => {
                const pid = parseInt(card.dataset.pid);
                const p = scoreMap[pid];
                if (!p) return;
                let overlay = card.querySelector('.fc-live-overlay');
                if (!overlay) {
                    overlay = document.createElement('div');
                    overlay.className = 'fc-live-overlay';
                    card.appendChild(overlay);
                }
                if (p.live_score != null) {
                    const cls = p.match_status === 'complete' ? 'complete' :
                        p.match_status === 'in_progress' ? 'live' : '';
                    overlay.innerHTML = `<span class="fc-live-score ${cls}">${p.live_score}</span>`;
                    overlay.style.display = '';
                } else {
                    overlay.style.display = 'none';
                }
            });
        },

        toggleLiveExpanded() {
            this._liveScoresExpanded = !this._liveScoresExpanded;
            document.getElementById('live-scores-players').style.display =
                this._liveScoresExpanded ? 'block' : 'none';
        },

        _renderLiveScores(data) {
            document.getElementById('live-round-label').textContent = `Round ${data.round}`;
            document.getElementById('live-total').textContent = data.total_live_score || 0;
            document.getElementById('live-projected').textContent =
                `Proj: ${data.projected_total ? data.projected_total.toFixed(0) : '-'}`;

            const parts = [];
            if (data.games_complete > 0) parts.push(`${data.games_complete} complete`);
            if (data.games_in_progress > 0) parts.push(`${data.games_in_progress} live`);
            if (data.games_upcoming > 0) parts.push(`${data.games_upcoming} upcoming`);
            document.getElementById('live-games-status').textContent = parts.join(' | ');

            // Player breakdown table
            const container = document.getElementById('live-scores-players');
            if (!data.players || !data.players.length) {
                container.innerHTML = '<div class="empty-state" style="padding:12px">No team loaded</div>';
                return;
            }

            // Sort: on-field first, then by score descending
            const sorted = [...data.players].sort((a, b) => {
                const aOnField = !a.position_slot.startsWith('BENCH') && !a.is_emergency;
                const bOnField = !b.position_slot.startsWith('BENCH') && !b.is_emergency;
                if (aOnField !== bOnField) return aOnField ? -1 : 1;
                return (b.live_score || 0) - (a.live_score || 0);
            });

            let html = '<table class="live-scores-table"><thead><tr>';
            html += '<th>Player</th><th>Slot</th><th>Opp</th>';
            html += '<th class="right">Score</th><th class="right">Proj</th><th>Status</th>';
            html += '</tr></thead><tbody>';

            for (const p of sorted) {
                const badges = [];
                if (p.is_captain) badges.push('<span style="color:var(--sc-gold);font-weight:700">C</span>');
                if (p.is_vice_captain) badges.push('<span style="color:var(--accent-cyan);font-weight:700">VC</span>');

                const scoreClass = p.match_status === 'complete' ? 'complete' :
                    p.match_status === 'in_progress' ? 'in_progress' : 'upcoming';
                const scoreVal = p.live_score != null ? p.live_score : '-';
                const displayScore = p.is_captain && p.live_score != null ?
                    `${p.live_score} (${p.live_score * 2})` : scoreVal;
                const projVal = p.projected_final != null ? p.projected_final.toFixed(0) : '-';

                html += '<tr>';
                html += `<td>${esc(p.player_name)} ${badges.join(' ')}</td>`;
                html += `<td style="color:var(--text-muted)">${p.position_slot}</td>`;
                html += `<td style="color:var(--text-muted)">${esc(p.opponent || '-')}</td>`;
                html += `<td class="right"><span class="live-score-badge ${scoreClass}">${displayScore}</span></td>`;
                html += `<td class="right" style="color:var(--text-secondary)">${projVal}</td>`;
                html += `<td><span class="match-status-pill ${scoreClass}">${p.match_status.replace('_', ' ')}</span></td>`;
                html += '</tr>';
            }

            html += '</tbody></table>';
            container.innerHTML = html;
        },

        async syncAndRefreshScores() {
            const btn = document.getElementById('live-sync-btn');
            btn.textContent = 'Syncing...';
            btn.disabled = true;
            try {
                // Trigger backend sync of all score sources
                await authFetch(`${API_BASE}/api/sync/trigger?source=footywire_scores`, {method: 'POST'});
                await authFetch(`${API_BASE}/api/sync/trigger?source=fanfooty`, {method: 'POST'});
                await authFetch(`${API_BASE}/api/sync/trigger?source=supercoach_round`, {method: 'POST'});
                // Wait a few seconds for sync to complete
                await new Promise(r => setTimeout(r, 5000));
                await this.loadLiveScores();
            } catch (e) {
                console.error('Score sync failed:', e);
            } finally {
                btn.textContent = 'Sync';
                btn.disabled = false;
            }
        },

        startLiveRefresh() {
            if (this._liveRefreshInterval) return;
            // Refresh every 30 seconds for live game updates
            this._liveRefreshInterval = setInterval(() => {
                if (App.state.connected) this.loadLiveScores();
            }, 30000);
        },

        stopLiveRefresh() {
            if (this._liveRefreshInterval) {
                clearInterval(this._liveRefreshInterval);
                this._liveRefreshInterval = null;
            }
        },

        // --- Emergency selection ---
        toggleEmergencyMode() {
            this._emergencyMode = !this._emergencyMode;
            const btn = document.getElementById('emg-edit-btn');
            const hint = document.getElementById('captain-hint');

            if (this._emergencyMode) {
                // Exit captain mode if active
                this._captainMode = null;
                document.getElementById('captain-btn').classList.remove('selecting');
                document.getElementById('vc-btn').classList.remove('selecting');

                // Load current emergencies
                this._emergencyPicks = [];
                if (this._lastTeamData) {
                    const emgSlots = this._lastTeamData.slots
                        .filter(s => s.is_emergency && s.emergency_order)
                        .sort((a, b) => a.emergency_order - b.emergency_order);
                    this._emergencyPicks = emgSlots.map(s => s.player_id);
                }

                btn.textContent = 'Done';
                btn.style.background = 'var(--accent-green)';
                btn.style.color = 'white';
                btn.style.borderColor = 'var(--accent-green)';
                hint.textContent = 'Click bench players to set E1-E4 (click again to remove)';
                hint.style.display = '';
                this._updateEmergencySlotDisplay();
            } else {
                // Save emergencies
                this._saveEmergencies();
                btn.textContent = 'Edit';
                btn.style.background = '';
                btn.style.color = '';
                btn.style.borderColor = '';
                hint.style.display = 'none';
            }

            // Re-render bench cards with selectable state
            if (this._lastTeamData) this.renderTeam(this._lastTeamData);
        },

        handleBenchEmergencyClick(playerId) {
            if (!this._emergencyMode) return;

            const idx = this._emergencyPicks.indexOf(playerId);
            if (idx !== -1) {
                // Remove this pick
                this._emergencyPicks.splice(idx, 1);
            } else if (this._emergencyPicks.length < 4) {
                // Check position coverage — SuperCoach requires one per line
                const player = this._lastTeamData.slots.find(s => s.player_id === playerId);
                if (!player) return;

                const playerPos = (player.position || '').split('/')[0].toUpperCase();
                const existingPositions = this._emergencyPicks.map(pid => {
                    const s = this._lastTeamData.slots.find(sl => sl.player_id === pid);
                    return s ? (s.position || '').split('/')[0].toUpperCase() : '';
                });

                // Allow if this position line isn't already covered, OR if dual-position
                if (existingPositions.includes(playerPos)) {
                    // Check if player has a second position
                    const positions = (player.position || '').split('/').map(p => p.trim().toUpperCase());
                    const hasUncovered = positions.some(p => !existingPositions.includes(p));
                    if (!hasUncovered) {
                        alert(`You already have an emergency for ${playerPos}. SuperCoach requires one per position line.`);
                        return;
                    }
                }

                this._emergencyPicks.push(playerId);
            } else {
                alert('Maximum 4 emergencies. Remove one first.');
                return;
            }

            this._updateEmergencySlotDisplay();
            if (this._lastTeamData) this.renderTeam(this._lastTeamData);
        },

        _updateEmergencySlotDisplay() {
            for (let i = 1; i <= 4; i++) {
                const el = document.getElementById(`emg-slot-${i}`);
                if (!el) continue;

                if (i <= this._emergencyPicks.length) {
                    const pid = this._emergencyPicks[i - 1];
                    const slot = this._lastTeamData
                        ? this._lastTeamData.slots.find(s => s.player_id === pid)
                        : null;
                    const name = slot ? slot.player_name.split(' ').pop() : `#${pid}`;
                    const pos = slot ? (slot.position || '').split('/')[0] : '';
                    el.textContent = `E${i}: ${name} (${pos})`;
                    el.className = 'emg-slot filled';
                } else {
                    el.textContent = `E${i}: -`;
                    el.className = this._emergencyMode ? 'emg-slot selecting' : 'emg-slot';
                }
            }
        },

        async _saveEmergencies() {
            if (!this._emergencyPicks.length && !this._lastTeamData?.slots.some(s => s.is_emergency)) {
                return; // Nothing to save
            }
            try {
                await authFetch(`${API_BASE}/api/team/emergency`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({emergencies: this._emergencyPicks}),
                });
                this.loadTeam();
            } catch (e) {
                alert('Error saving emergencies: ' + e.message);
            }
        },

        async quickAddEmergency(playerId) {
            this.closeCardMenu();
            // Get current emergencies
            const emgSlots = (this._lastTeamData?.slots || [])
                .filter(s => s.is_emergency && s.emergency_order)
                .sort((a, b) => a.emergency_order - b.emergency_order);
            const picks = emgSlots.map(s => s.player_id);

            if (picks.length >= 4) {
                alert('Already have 4 emergencies. Remove one first.');
                return;
            }
            picks.push(playerId);

            try {
                await authFetch(`${API_BASE}/api/team/emergency`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({emergencies: picks}),
                });
                this.loadTeam();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        async quickRemoveEmergency(playerId) {
            this.closeCardMenu();
            const emgSlots = (this._lastTeamData?.slots || [])
                .filter(s => s.is_emergency && s.emergency_order)
                .sort((a, b) => a.emergency_order - b.emergency_order);
            const picks = emgSlots.map(s => s.player_id).filter(id => id !== playerId);

            try {
                await authFetch(`${API_BASE}/api/team/emergency`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({emergencies: picks}),
                });
                this.loadTeam();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        _updateEmergencyPickerFromData(data) {
            // Update emergency slot display from team data
            const emgSlots = data.slots
                .filter(s => s.is_emergency && s.emergency_order)
                .sort((a, b) => a.emergency_order - b.emergency_order);

            for (let i = 1; i <= 4; i++) {
                const el = document.getElementById(`emg-slot-${i}`);
                if (!el) continue;

                if (i <= emgSlots.length) {
                    const s = emgSlots[i - 1];
                    const name = s.player_name.split(' ').pop();
                    const pos = (s.position || '').split('/')[0];
                    el.textContent = `E${i}: ${name} (${pos})`;
                    el.className = 'emg-slot filled';
                } else {
                    el.textContent = `E${i}: -`;
                    el.className = 'emg-slot';
                }
            }
        },

        async clearTeam() {
            if (!confirm('Clear your entire team?')) return;
            try {
                await authFetch(`${API_BASE}/api/team/clear`, {method: 'POST'});
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
                const res = await authFetch(`${API_BASE}/api/team/import-csv`, {method: 'POST', body: form});
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
                const res = await authFetch(`${API_BASE}/api/team`);
                const data = await res.json();
                this.renderTeam(data);
            } catch (e) {
                console.error('Failed to load team:', e);
            }
        },

        renderTeam(data) {
            document.getElementById('team-count').textContent = data.player_count;
            document.getElementById('salary-total').textContent = data.salary_total
                ? `$${data.salary_total.toLocaleString()}`
                : '$0';

            this._lastTeamData = data;

            // Update salary cap bar
            const used = data.salary_total || 0;
            const pct = Math.min((used / this.SALARY_CAP) * 100, 100);
            const remaining = this.SALARY_CAP - used;
            const fill = document.getElementById('salary-cap-fill');
            const label = document.getElementById('salary-cap-label');

            if (fill) {
                fill.style.width = pct + '%';
                fill.className = 'salary-cap-fill';
                if (pct > 95 || remaining < 0) fill.classList.add('cap-over');
                else if (pct > 80) fill.classList.add('cap-warn');
            }
            if (label) {
                if (remaining < 0) {
                    label.textContent = `Over cap by $${Math.abs(remaining).toLocaleString()}`;
                    label.style.color = 'var(--accent-red)';
                } else {
                    label.textContent = `Remaining: $${remaining.toLocaleString()}`;
                    label.style.color = '';
                }
            }

            this._renderListView(data);
            this._renderFieldView(data);
            this._updateCaptainPicker(data);
            if (!this._emergencyMode) {
                this._updateEmergencyPickerFromData(data);
            }
        },

        _renderListView(data) {
            const container = document.getElementById('list-view');
            if (!data.slots.length) {
                container.innerHTML = '<div class="empty-state">No team loaded yet. Search and add players, or import a CSV.</div>';
                return;
            }

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
                    if (s.is_emergency && s.emergency_order) {
                        badges.push(`<span class="badge badge-emg">E${s.emergency_order}</span>`);
                    } else if (s.is_emergency) {
                        badges.push('<span class="badge badge-emg">EMG</span>');
                    }

                    const salary = s.salary ? `$${s.salary.toLocaleString()}` : '';
                    const score = s.last_score != null ? s.last_score : (s.sc_avg != null ? s.sc_avg : '-');

                    html += `<div class="team-slot">`;
                    html += `<span class="slot-label">${s.position_slot}</span>`;
                    html += `<span class="player-name">${this._esc(s.player_name)}${badges.join('')}</span>`;
                    html += `<span class="player-team">${this._esc(s.team)}</span>`;
                    html += `<span class="player-score" style="font-size:12px;font-weight:700;width:40px;text-align:right;margin-right:4px">${score}</span>`;
                    html += `<span class="player-salary">${salary}</span>`;
                    const cActive = s.is_captain ? ' style="background:var(--sc-gold);color:#1a1a1a;border-color:var(--sc-gold);opacity:1"' : '';
                    const vcActive = s.is_vice_captain ? ' style="background:var(--accent-cyan);color:#1a1a1a;border-color:var(--accent-cyan);opacity:1"' : '';

                    html += `<div class="actions">`;
                    html += `<button class="btn btn-sm"${cActive} onclick="App.Team.setCaptain(${s.player_id})" title="Set Captain">C</button>`;
                    html += `<button class="btn btn-sm"${vcActive} onclick="App.Team.setVC(${s.player_id})" title="Set Vice Captain">VC</button>`;
                    html += `<button class="btn btn-sm btn-danger" onclick="App.Team.removePlayer(${s.id})" title="Remove">x</button>`;
                    html += `</div>`;
                    html += `</div>`;
                }
                html += `</div>`;
            }

            container.innerHTML = html;
        },

        _renderFieldView(data) {
            const container = document.getElementById('field-view');

            // Build occupied slot map
            const occupied = {};
            for (const s of data.slots) {
                occupied[s.position_slot] = s;
            }

            // Define all slot positions
            const allSlots = {
                DEF: Array.from({length: 6}, (_, i) => `DEF${i+1}`),
                MID: Array.from({length: 8}, (_, i) => `MID${i+1}`),
                RUC: ['RUC1', 'RUC2'],
                FWD: Array.from({length: 6}, (_, i) => `FWD${i+1}`),
                FLEX: ['FLEX1'],
                BENCH: Array.from({length: 8}, (_, i) => `BENCH${i+1}`),
            };

            // Helper: render cards for a group (filled + empty placeholders)
            const renderZoneCards = (slotNames) => {
                let cards = '';
                for (const slotName of slotNames) {
                    if (occupied[slotName]) {
                        cards += this._renderFieldCard(occupied[slotName]);
                    } else {
                        cards += `<div class="field-card-empty" onclick="App.Team.setPendingSlot('${slotName}')">
                            <span class="empty-plus">+</span>
                            <span class="empty-label">${slotName}</span>
                        </div>`;
                    }
                }
                return cards;
            };

            const renderBenchCards = (slotNames) => {
                let cards = '';
                for (const slotName of slotNames) {
                    if (occupied[slotName]) {
                        cards += this._renderBenchCard(occupied[slotName]);
                    } else {
                        cards += `<div class="bench-card-empty" onclick="App.Team.setPendingSlot('${slotName}')">
                            <span class="empty-plus">+</span>
                            <span class="empty-label">${slotName}</span>
                        </div>`;
                    }
                }
                return cards;
            };

            let html = '<div class="field-view-wrapper">';
            html += '<div class="field-pitch">';
            html += '<div class="field-centre-circle"></div>';
            html += '<div class="field-centre-line"></div>';

            // DEF zone
            html += '<div class="field-zone">';
            html += '<div class="field-zone-label">Defenders</div>';
            html += '<div class="field-zone-cards">';
            html += renderZoneCards(allSlots.DEF);
            html += '</div></div>';

            // MID zone — split into two rows
            html += '<div class="field-zone">';
            html += '<div class="field-zone-label">Midfielders</div>';
            html += '<div class="field-zone-cards">';
            html += renderZoneCards(allSlots.MID.slice(0, 4));
            html += '</div>';
            html += '<div class="field-zone-cards">';
            html += renderZoneCards(allSlots.MID.slice(4));
            html += '</div>';
            html += '</div>';

            // RUC zone
            html += '<div class="field-zone">';
            html += '<div class="field-zone-label">Rucks</div>';
            html += '<div class="field-zone-cards">';
            html += renderZoneCards(allSlots.RUC);
            html += '</div></div>';

            // FWD zone
            html += '<div class="field-zone">';
            html += '<div class="field-zone-label">Forwards</div>';
            html += '<div class="field-zone-cards">';
            html += renderZoneCards(allSlots.FWD);
            html += '</div></div>';

            // FLEX zone
            html += '<div class="field-zone">';
            html += '<div class="field-zone-label">Flex</div>';
            html += '<div class="field-zone-cards">';
            html += renderZoneCards(allSlots.FLEX);
            html += '</div></div>';

            html += '</div>'; // .field-pitch

            // Bench sidebar
            html += '<div class="field-bench">';
            html += '<div class="field-bench-header">';
            html += '<span class="field-bench-title">Bench</span>';
            html += '</div>';
            html += renderBenchCards(allSlots.BENCH);
            html += '</div>'; // .field-bench

            html += '</div>'; // .field-view-wrapper
            container.innerHTML = html;
        },

        _renderFieldCard(s) {
            const teamColor = TEAM_COLORS[s.team] || '#444';
            const teamAbbr = TEAM_ABBREVS[s.team] || (s.team || '').substring(0, 3).toUpperCase();
            const salary = s.salary ? `$${(s.salary / 1000).toFixed(0)}k` : '';
            const score = this._getDisplayScore(s);
            const displayName = this._abbreviateName(s.player_name);
            const posLabel = s.position ? s.position.replace('/', ' | ') : '';

            const selectable = this._captainMode ? ' captain-selectable' : '';
            const clickHandler = this._captainMode
                ? `onclick="App.Team.handleCardClick(${s.player_id})"`
                : '';
            let html = `<div class="field-card${selectable}" data-pid="${s.player_id}" style="border-left-color:${teamColor}" oncontextmenu="App.Team.showCardMenu(event, ${s.id}, ${s.player_id})" ${clickHandler}>`;

            // Remove button (top-right, shown on hover)
            html += `<button class="fc-remove" onclick="event.stopPropagation();App.Team.removePlayer(${s.id})" title="Remove">&times;</button>`;

            if (s.is_captain) {
                html += '<div class="fc-badge fc-badge-c">C</div>';
            } else if (s.is_vice_captain) {
                html += '<div class="fc-badge fc-badge-vc">VC</div>';
            }

            html += `<div class="fc-score">${score}</div>`;
            html += `<div class="fc-name">${this._esc(displayName)}</div>`;
            html += '<div class="fc-meta">';
            html += `<span class="fc-team">${this._esc(teamAbbr)}${posLabel ? ' | ' + posLabel : ''}</span>`;
            html += `<span class="fc-salary">${salary}</span>`;
            html += '</div>';

            // Lineup status indicator
            const lineup = this._getLineupStatus(s);
            if (lineup) {
                html += `<div class="fc-lineup-status ${lineup.status}" title="${this._esc(lineup.tooltip)}">${lineup.label}</div>`;
                if (lineup.status === 'injured') {
                    html += `<div class="fc-injury-tooltip">${this._esc(lineup.tooltip)}</div>`;
                }
            }

            html += '</div>';
            return html;
        },

        _renderBenchCard(s) {
            const teamColor = TEAM_COLORS[s.team] || '#444';
            const teamAbbr = TEAM_ABBREVS[s.team] || (s.team || '').substring(0, 3).toUpperCase();
            const salary = s.salary ? `$${(s.salary / 1000).toFixed(0)}k` : '';
            const score = this._getDisplayScore(s);
            const displayName = this._abbreviateName(s.player_name);

            let selectable = this._captainMode ? ' captain-selectable' : '';
            let clickHandler = this._captainMode
                ? `onclick="App.Team.handleCardClick(${s.player_id})"`
                : '';

            // Emergency mode: bench cards are clickable
            if (this._emergencyMode) {
                selectable = ' emg-selectable';
                clickHandler = `onclick="App.Team.handleBenchEmergencyClick(${s.player_id})"`;
            }

            let html = `<div class="bench-card${selectable}" data-pid="${s.player_id}" style="border-left-color:${teamColor}" oncontextmenu="App.Team.showCardMenu(event, ${s.id}, ${s.player_id})" ${clickHandler}>`;

            // Remove button (top-right, shown on hover)
            html += `<button class="fc-remove" onclick="event.stopPropagation();App.Team.removePlayer(${s.id})" title="Remove">&times;</button>`;

            // Emergency badge — show order number
            const emgIdx = this._emergencyMode
                ? this._emergencyPicks.indexOf(s.player_id)
                : -1;
            if (emgIdx !== -1) {
                html += `<div class="fc-emg">E${emgIdx + 1}</div>`;
            } else if (!this._emergencyMode && s.is_emergency && s.emergency_order) {
                html += `<div class="fc-emg">E${s.emergency_order}</div>`;
            }

            html += `<div class="fc-score">${score}</div>`;
            html += `<div class="fc-name">${this._esc(displayName)}</div>`;
            html += '<div class="fc-meta">';
            html += `<span class="fc-team">${this._esc(teamAbbr)}</span>`;
            html += `<span class="fc-salary">${salary}</span>`;
            html += '</div>';

            // Lineup status indicator
            const lineup = this._getLineupStatus(s);
            if (lineup) {
                html += `<div class="fc-lineup-status ${lineup.status}" title="${this._esc(lineup.tooltip)}">${lineup.label}</div>`;
                if (lineup.status === 'injured') {
                    html += `<div class="fc-injury-tooltip">${this._esc(lineup.tooltip)}</div>`;
                }
            }

            html += '</div>';
            return html;
        },

        _abbreviateName(name) {
            if (!name) return '';
            const parts = name.split(' ');
            if (parts.length < 2) return name;
            const initial = parts[0][0];
            let surname = parts.slice(1).join(' ');
            if (surname.length > 13) {
                surname = surname.substring(0, 12) + '...';
            }
            return `${initial}. ${surname}`;
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
                authFetch(`${API_BASE}/api/analytics/projections?round=${round}&team_only=true`).then(r => r.json()),
                authFetch(`${API_BASE}/api/analytics/captain?round=${round}&top_n=10`).then(r => r.json()),
                authFetch(`${API_BASE}/api/analytics/trades?round=${round}`).then(r => r.json()),
                authFetch(`${API_BASE}/api/analytics/injuries`).then(r => r.json()),
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
                const res = await authFetch(`${API_BASE}/api/ai/weekly`);
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
                const res = await authFetch(`${API_BASE}/api/ai/captain?round=${round}`);
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
                const res = await authFetch(`${API_BASE}/api/ai/trades?round=${round}`);
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
                const res = await authFetch(`${API_BASE}/api/ai/chat`, {
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
