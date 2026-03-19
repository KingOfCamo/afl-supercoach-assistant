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
        selectedRound: null,  // round the user is viewing (null = current)
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

            // Show bye alerts
            if (c.bye_alerts && c.bye_alerts.length) {
                const banner = document.getElementById('bye-alert-banner');
                const content = document.getElementById('bye-alert-content');
                content.innerHTML = c.bye_alerts.map(a =>
                    `<div class="bye-alert-item">&#9888; ${esc(a)}</div>`
                ).join('');
                banner.style.display = '';
            }

            // Load team + live scores on first successful connection
            if (!this._initialLoad) {
                this._initialLoad = true;
                this.Team.loadTeam();
                this.Team.loadLiveScores();
                this.Team.loadFixtures();
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
        document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
        const tab = document.querySelector(`.nav-tab[data-section="${name}"]`);
        if (tab) tab.classList.add('active');

        // Update sections
        document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
        document.getElementById(`section-${name}`).classList.add('active');

        this.state.currentSection = name;

        // Load data when switching sections
        if (name === 'briefing') this.Briefing.load();
        if (name === 'dashboard') this.Dashboard.loadAll();
        if (name === 'byes') this.Byes.loadAll();
        if (name === 'warroom') this.WarRoom.loadAll();
        if (name === 'ownership') this.Ownership.loadAll();
        if (name === 'tracker') this.Tracker.loadAll();
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
        _scoreView: 'last', // 'last', 'live', 'projected', 'average'
        _liveScoresExpanded: false,
        _liveRefreshInterval: null,
        _swapSource: null, // {slot data object} of first selected player
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

                for (const p of data.players) {
                    const affordable = !p.salary || p.salary <= remaining;
                    const salaryClass = p.salary ? (affordable ? 'salary-affordable' : 'salary-over') : '';
                    const salaryStr = p.salary ? `$${p.salary.toLocaleString()}` : '-';
                    const avgStr = p.sc_avg ? p.sc_avg.toFixed(0) : '-';

                    let btnHtml;
                    if (p.is_on_team) {
                        btnHtml = '<span class="on-team-check">&#10003;</span>';
                    } else if (this._pendingSlot) {
                        btnHtml = `<button class="btn btn-sm btn-success" onclick="App.Team.addPlayer(${p.id}, '${this._pendingSlot}')">+ ${this._pendingSlot}</button>`;
                    } else {
                        const autoSlot = this.autoAssignSlot(p.position || '');
                        btnHtml = `<div style="display:flex;gap:2px;">`;
                        if (autoSlot) {
                            btnHtml += `<button class="btn btn-sm btn-success" onclick="App.Team.addPlayer(${p.id}, '${autoSlot}')" title="Auto: ${autoSlot}">+ Add</button>`;
                        }
                        btnHtml += `<button class="btn btn-sm" onclick="App.Team.showSlotPicker(${p.id}, '${p.position || ''}', this)" title="Pick slot" style="padding:2px 5px;">&#9660;</button>`;
                        btnHtml += `</div>`;
                    }

                    html += `<div class="search-card">
                        <div class="search-card-top">
                            <div class="search-card-name">${this._esc(p.name)}</div>
                            <div class="search-card-action">${btnHtml}</div>
                        </div>
                        <div class="search-card-meta">
                            <span class="search-card-team">${this._esc(p.team)}</span>
                            <span class="search-card-pos">${this._esc(p.position || '-')}</span>
                            <span class="search-card-salary ${salaryClass}">${salaryStr}</span>
                            <span class="search-card-avg">Avg ${avgStr}</span>
                        </div>
                    </div>`;
                }
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
            // Captain/VC selection mode
            if (this._captainMode) {
                if (this._captainMode === 'captain') {
                    this.setCaptain(playerId);
                } else {
                    this.setVC(playerId);
                }
                this._captainMode = null;
                document.getElementById('captain-btn').classList.remove('selecting');
                document.getElementById('vc-btn').classList.remove('selecting');
                document.getElementById('captain-hint').style.display = 'none';
                return;
            }

            // Emergency mode handled elsewhere
            if (this._emergencyMode) return;

            // Swap mode
            if (!this._lastTeamData) return;
            const clickedSlot = this._lastTeamData.slots.find(s => s.player_id === playerId);
            if (!clickedSlot) return;

            if (!this._swapSource) {
                // First click — select source
                this._swapSource = clickedSlot;
                this._highlightSwapTargets(clickedSlot);
            } else if (this._swapSource.player_id === playerId) {
                // Clicked same player — cancel
                this.cancelSwap();
            } else {
                // Second click — execute swap
                this._executeSwap(this._swapSource.position_slot, clickedSlot.position_slot);
            }
        },

        cancelSwap() {
            this._swapSource = null;
            document.querySelectorAll('.swap-source, .swap-valid, .swap-invalid').forEach(el => {
                el.classList.remove('swap-source', 'swap-valid', 'swap-invalid');
            });
        },

        _canSwapTo(sourcePosition, targetSlotName) {
            const targetPos = targetSlotName.startsWith('BENCH') ? 'BENCH'
                : targetSlotName.startsWith('FLEX') ? 'FLEX'
                : targetSlotName.replace(/\d+$/, '');
            if (targetPos === 'BENCH' || targetPos === 'FLEX') return true;
            if (!sourcePosition) return false;
            return sourcePosition.split('/').map(p => p.trim().toUpperCase()).includes(targetPos);
        },

        _highlightSwapTargets(source) {
            // Mark source card
            const sourceCard = document.querySelector(`[data-pid="${source.player_id}"]`);
            if (sourceCard) sourceCard.classList.add('swap-source');

            // Mark all other cards as valid or invalid
            if (!this._lastTeamData) return;
            for (const s of this._lastTeamData.slots) {
                if (s.player_id === source.player_id) continue;
                const card = document.querySelector(`[data-pid="${s.player_id}"]`);
                if (!card) continue;

                const sourceCanGoToTarget = this._canSwapTo(source.position, s.position_slot);
                const targetCanGoToSource = this._canSwapTo(s.position, source.position_slot);

                if (sourceCanGoToTarget && targetCanGoToSource) {
                    card.classList.add('swap-valid');
                } else {
                    card.classList.add('swap-invalid');
                }
            }
        },

        async _executeSwap(slotA, slotB) {
            try {
                const res = await authFetch(`${API_BASE}/api/team/swap`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({slot_a: slotA, slot_b: slotB}),
                });
                const data = await res.json();
                if (!res.ok) {
                    this._showToast(data.detail || 'Swap failed', 'error');
                    this.cancelSwap();
                    return;
                }
                this._showToast(`Swapped ${data.slot_a.player_name} ↔ ${data.slot_b.player_name}`, 'success');
                this._swapSource = null;
                await this.loadTeam();
            } catch (e) {
                this._showToast('Swap failed: ' + e.message, 'error');
                this.cancelSwap();
            }
        },

        _showToast(message, type) {
            const existing = document.querySelector('.swap-toast');
            if (existing) existing.remove();
            const toast = document.createElement('div');
            toast.className = `swap-toast swap-toast-${type}`;
            toast.textContent = message;
            document.body.appendChild(toast);
            setTimeout(() => toast.classList.add('visible'), 10);
            setTimeout(() => { toast.classList.remove('visible'); setTimeout(() => toast.remove(), 300); }, 3000);
        },

        // --- AI Optimise ---
        _optimiseData: null,
        _optimiseChecked: [],

        async openOptimiser() {
            const overlay = document.getElementById('optimise-overlay');
            const swapsContainer = document.getElementById('optimise-swaps');
            const scoreContainer = document.getElementById('optimise-score');
            const actionsContainer = document.getElementById('optimise-actions');

            overlay.style.display = 'flex';
            swapsContainer.innerHTML = '<div class="loading"><div class="spinner"></div><div>Analysing lineup...</div></div>';
            scoreContainer.innerHTML = '';
            actionsContainer.innerHTML = '';

            const round = App.state.config ? App.state.config.current_round : 1;
            document.getElementById('optimise-title').textContent = `Optimised Lineup — Round ${round}`;

            try {
                const res = await authFetch(`${API_BASE}/api/team/optimise?round_num=${round}`);
                const data = await res.json();
                this._optimiseData = data;
                this._optimiseChecked = data.swaps.map((_, i) => true);
                this._renderOptimisePanel(data);
            } catch (e) {
                swapsContainer.innerHTML = `<div class="empty-state">Failed to load: ${e.message}</div>`;
            }
        },

        closeOptimiser() {
            document.getElementById('optimise-overlay').style.display = 'none';
            this._optimiseData = null;
        },

        _renderOptimisePanel(data) {
            const scoreContainer = document.getElementById('optimise-score');
            const swapsContainer = document.getElementById('optimise-swaps');
            const actionsContainer = document.getElementById('optimise-actions');

            if (!data.swaps || !data.swaps.length) {
                scoreContainer.innerHTML = `<div class="optimise-score-bar">Your lineup is already optimal for this round.</div>`;
                swapsContainer.innerHTML = '';
                actionsContainer.innerHTML = '<button class="btn" onclick="App.Team.closeOptimiser()">Close</button>';
                return;
            }

            const sign = data.improvement >= 0 ? '+' : '';
            scoreContainer.innerHTML = `
                <div class="optimise-score-bar">
                    <span>Current: <strong>${data.current_total.toFixed(0)}</strong> pts</span>
                    <span class="optimise-arrow">→</span>
                    <span>Optimised: <strong class="optimise-highlight">${data.optimal_total.toFixed(0)}</strong> pts</span>
                    <span class="optimise-delta ${data.improvement >= 0 ? 'positive' : 'negative'}">(${sign}${data.improvement.toFixed(0)})</span>
                </div>
            `;

            let html = '';
            data.swaps.forEach((swap, i) => {
                const checked = this._optimiseChecked[i];
                html += `<div class="optimise-swap-card ${checked ? 'checked' : 'unchecked'}">`;
                html += `<div class="optimise-swap-header">`;
                html += `<span class="optimise-swap-num">SWAP ${i + 1}</span>`;
                html += `<div class="optimise-swap-toggle">`;
                html += `<button class="btn btn-sm ${checked ? 'btn-success' : ''}" onclick="App.Team.toggleOptimiseSwap(${i}, true)">&#10003;</button>`;
                html += `<button class="btn btn-sm ${!checked ? 'btn-danger' : ''}" onclick="App.Team.toggleOptimiseSwap(${i}, false)">&#10005;</button>`;
                html += `</div></div>`;

                // OUT player
                html += `<div class="optimise-out">`;
                html += `<span class="optimise-label-out">OUT</span>`;
                html += `<span class="optimise-player">${esc(swap.out_player)} <small>(${esc(swap.out_slot)})</small></span>`;
                html += `<span class="optimise-team">${esc(swap.out_team)}</span>`;
                html += `<span class="optimise-reason">${esc(swap.out_reason)}</span>`;
                html += `<span class="optimise-pts">${swap.out_projected.toFixed(0)} pts</span>`;
                html += `</div>`;

                // IN player
                html += `<div class="optimise-in">`;
                html += `<span class="optimise-label-in">IN</span>`;
                html += `<span class="optimise-player">${esc(swap.in_player)} <small>(${esc(swap.in_slot)})</small></span>`;
                html += `<span class="optimise-team">${esc(swap.in_team)}</span>`;
                html += `<span class="optimise-pts">${swap.in_projected.toFixed(0)} pts</span>`;
                html += `</div>`;

                html += `<div class="optimise-impact">Impact: <strong>+${swap.impact.toFixed(0)}</strong> pts</div>`;
                html += `</div>`;
            });

            swapsContainer.innerHTML = html;

            const checkedCount = this._optimiseChecked.filter(Boolean).length;
            actionsContainer.innerHTML = `
                <button class="btn btn-primary" onclick="App.Team.acceptOptimise('all')">Accept All (${data.swaps.length})</button>
                <button class="btn btn-success" onclick="App.Team.acceptOptimise('selected')">Accept Selected (${checkedCount})</button>
                <button class="btn" onclick="App.Team.closeOptimiser()">Dismiss</button>
            `;
        },

        toggleOptimiseSwap(index, value) {
            this._optimiseChecked[index] = value;
            if (this._optimiseData) this._renderOptimisePanel(this._optimiseData);
        },

        async acceptOptimise(mode) {
            if (!this._optimiseData) return;
            const swaps = this._optimiseData.swaps;
            const toExecute = mode === 'all'
                ? swaps
                : swaps.filter((_, i) => this._optimiseChecked[i]);

            if (!toExecute.length) {
                this._showToast('No swaps selected', 'error');
                return;
            }

            let executed = 0;
            for (const swap of toExecute) {
                try {
                    const res = await authFetch(`${API_BASE}/api/team/swap`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({slot_a: swap.slot_a, slot_b: swap.slot_b}),
                    });
                    if (res.ok) executed++;
                } catch (e) {
                    console.error('Swap failed:', e);
                }
            }

            this.closeOptimiser();
            const totalImpact = toExecute.reduce((sum, s) => sum + s.impact, 0);
            this._showToast(`${executed} swap${executed > 1 ? 's' : ''} applied. +${totalImpact.toFixed(0)} projected points`, 'success');
            await this.loadTeam();
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
            // Priority 0: Bye round — team not playing this round
            if (s.is_on_bye) {
                return {
                    status: 'bye',
                    label: 'BYE',
                    tooltip: `${s.team} has a bye this round`,
                };
            }

            // Priority 1: Injury (but still check if they played below)
            const isInjured = !!s.injury;

            // Priority 2: Live match data — played/playing overrides lineup status
            // Only use live data when there's a real score (> 0), since the API
            // returns 0 for players whose game hasn't started yet
            if (this._liveData && this._liveData.players) {
                const lp = this._liveData.players.find(p => p.player_id === s.player_id);
                if (lp) {
                    if (lp.match_status === 'complete' && lp.live_score != null && lp.live_score > 0) {
                        return { status: 'played', label: '✓', tooltip: `Played — ${lp.live_score}pts` };
                    }
                    if (lp.match_status === 'in_progress' && lp.live_score != null && lp.live_score > 0) {
                        return { status: 'playing', label: '●', tooltip: `Live — ${lp.live_score}pts` };
                    }
                    if (lp.match_status === 'complete' && (lp.live_score == null || lp.live_score === 0)) {
                        return { status: 'not-playing', label: '✕', tooltip: 'Did not play (DNP)' };
                    }
                }
            }

            // Priority 3: Injury (only if no live match result yet)
            if (isInjured) {
                return {
                    status: 'injured',
                    label: '⚠',
                    tooltip: `${s.injury.type || 'Injured'} — ${s.injury.return || 'TBD'}`,
                };
            }

            // Priority 4: AFL.com.au lineup announcement (pre-game only)
            // Plain tick (no circle) for named players
            if (s.lineup_status === 'NAMED') {
                const opp = s.lineup_opponent ? ` v ${s.lineup_opponent}` : '';
                return {
                    status: 'named',
                    label: '✓',
                    tooltip: `Named${opp}${s.lineup_position ? ' (' + s.lineup_position + ')' : ''}`,
                };
            }
            if (s.lineup_status === 'EMERGENCY') {
                return {
                    status: 'match-emergency',
                    label: 'E',
                    tooltip: `Named as match-day emergency`,
                };
            }

            return null; // No lineup data yet
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
                // Sync scores + fixtures + byes + lineups in parallel
                const scoreSync = authFetch(`${API_BASE}/api/sync/scores`, {method: 'POST'});
                const lineupSync = authFetch(`${API_BASE}/api/sync/trigger?source=afl_lineups`, {method: 'POST'});

                await Promise.all([scoreSync, lineupSync]);

                // Re-fetch config to get updated current_round
                const configRes = await authFetch(`${API_BASE}/api/config`, {signal: AbortSignal.timeout(5000)});
                App.state.config = await configRes.json();
                const c = App.state.config;
                document.getElementById('sidebar-info').textContent =
                    `${c.season} | Round ${c.current_round}`;
                document.getElementById('header-info').textContent =
                    `Round ${c.current_round} | ${c.season} | ${c.trades_remaining} trades left`;

                // Reload all data with fresh round info
                await Promise.all([
                    this.loadLiveScores(),
                    this.loadTeam(),
                    this.loadFixtures(),
                ]);
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

        // --- Fixtures ---
        _fixtureData: null,

        async loadFixtures(roundOverride) {
            const round = roundOverride || App.state.selectedRound
                || (App.state.config ? App.state.config.current_round : 1);
            try {
                const res = await authFetch(`${API_BASE}/api/fixtures/db-round?round_num=${round}`);
                const data = await res.json();
                this._fixtureData = data;
                this._renderFixtureWidget();
            } catch (e) {
                console.error('Failed to load fixtures:', e);
            }
        },

        changeRound(delta) {
            const current = App.state.config ? App.state.config.current_round : 1;
            const maxRound = (this._fixtureData && this._fixtureData.max_round) || 24;
            const viewing = App.state.selectedRound || current;
            const next = Math.max(1, Math.min(maxRound, viewing + delta));
            App.state.selectedRound = next;
            this.loadFixtures(next);
        },

        goToCurrentRound() {
            App.state.selectedRound = null;
            this.loadFixtures();
        },

        _renderFixtureWidget() {
            const container = document.getElementById('fixture-widget');
            if (!container || !this._fixtureData) return;

            const data = this._fixtureData;
            const matches = data.matches || [];
            const currentRound = App.state.config ? App.state.config.current_round : 1;
            const viewingRound = data.round;
            const isCurrent = viewingRound === currentRound;
            const maxRound = data.max_round || 24;

            // Round navigation header
            let html = '<div class="fixture-nav">';
            html += `<button class="fix-nav-btn" onclick="App.Team.changeRound(-1)" ${viewingRound <= 1 ? 'disabled' : ''}>&#9664;</button>`;
            html += `<span class="fix-nav-round${isCurrent ? ' fix-current' : ''}" onclick="App.Team.goToCurrentRound()">`;
            html += `Round ${viewingRound}`;
            if (isCurrent) html += ' <small>(Current)</small>';
            html += '</span>';
            html += `<button class="fix-nav-btn" onclick="App.Team.changeRound(1)" ${viewingRound >= maxRound ? 'disabled' : ''}>&#9654;</button>`;
            html += '</div>';

            // Bye teams banner
            if (data.bye_teams && data.bye_teams.length) {
                html += '<div class="fix-bye-banner">';
                html += '<span class="fix-bye-label">BYE</span> ';
                html += data.bye_teams.map(t => {
                    const abbr = TEAM_ABBREVS[t] || t;
                    const color = TEAM_COLORS[t] || '#666';
                    return `<span class="fix-bye-team" style="border-color:${color}">${this._esc(abbr)}</span>`;
                }).join(' ');
                html += '</div>';
            }

            if (!matches.length && (!data.bye_teams || !data.bye_teams.length)) {
                html += '<div class="fixture-empty">No fixtures available</div>';
                container.innerHTML = html;
                return;
            }

            html += '<div class="fixture-list">';

            for (const m of matches) {
                const homeColor = TEAM_COLORS[m.home_team] || '#444';
                const awayColor = TEAM_COLORS[m.away_team] || '#444';
                const homeAbbr = TEAM_ABBREVS[m.home_team] || (m.home_team || '').substring(0, 3).toUpperCase();
                const awayAbbr = TEAM_ABBREVS[m.away_team] || (m.away_team || '').substring(0, 3).toUpperCase();

                let timeStr = '';
                if (m.date) {
                    const d = new Date(m.date);
                    const day = d.toLocaleDateString('en-AU', { weekday: 'short' });
                    const time = d.toLocaleTimeString('en-AU', { hour: 'numeric', minute: '2-digit' });
                    timeStr = `${day} ${time}`;
                }

                let statusClass = '';
                let scoreHtml = '';
                if (m.status === 'CONCLUDED' || m.is_complete) {
                    statusClass = 'concluded';
                    scoreHtml = `<span class="fix-score">${m.home_score ?? '-'} - ${m.away_score ?? '-'}</span>`;
                } else if (m.status === 'LIVE' || m.status === 'PLAYING') {
                    statusClass = 'live';
                    scoreHtml = `<span class="fix-score fix-live">${m.home_score ?? 0} - ${m.away_score ?? 0}</span>`;
                } else {
                    scoreHtml = `<span class="fix-time">${timeStr || 'TBC'}</span>`;
                }

                html += `<div class="fixture-row ${statusClass}">`;
                html += `<div class="fix-team fix-home" style="--tc:${homeColor}">`;
                html += `<span class="fix-abbr">${homeAbbr}</span>`;
                html += `<span class="fix-dot" style="background:${homeColor}"></span>`;
                html += `</div>`;
                html += `<div class="fix-centre">${scoreHtml}</div>`;
                html += `<div class="fix-team fix-away" style="--tc:${awayColor}">`;
                html += `<span class="fix-dot" style="background:${awayColor}"></span>`;
                html += `<span class="fix-abbr">${awayAbbr}</span>`;
                html += `</div>`;
                html += `</div>`;
                html += `<div class="fix-venue">${m.venue || ''}</div>`;
            }

            html += '</div>';
            container.innerHTML = html;
        },

        // --- Emergency selection ---
        // --- Emergency Panel (position-based grid) ---

        renderEmergencyPanel() {
            const grid = document.getElementById('emergency-grid');
            const countEl = document.getElementById('emg-count');
            if (!grid || !this._lastTeamData) return;

            const slots = this._lastTeamData.slots;
            const emergencies = slots.filter(s => s.is_emergency).sort((a, b) => (a.emergency_order || 0) - (b.emergency_order || 0));
            if (countEl) countEl.textContent = emergencies.length;

            const positions = ['DEF', 'MID', 'RUC', 'FWD'];
            let html = '';

            for (const pos of positions) {
                const posEmgs = emergencies.filter(s => s.emergency_position === pos || (!s.emergency_position && (s.position || '').split('/')[0].toUpperCase() === pos));
                html += `<div class="emg-col">`;
                html += `<div class="emg-col-header">${pos}</div>`;

                for (let i = 0; i < 2; i++) {
                    const emg = posEmgs[i];
                    if (emg) {
                        const name = emg.player_name.split(' ').pop();
                        html += `<div class="emg-slot-card filled">`;
                        html += `<div class="emg-slot-top">`;
                        html += `<span class="emg-slot-name">${this._esc(name)}</span>`;
                        html += `<button class="emg-remove" onclick="App.Team.removeEmergency(${emg.player_id})">&#10005;</button>`;
                        html += `</div>`;
                        html += `<div class="emg-slot-pos">${this._esc(emg.position || pos)}</div>`;
                        html += `</div>`;
                    } else {
                        html += `<div class="emg-slot-card empty">`;
                        html += `<button class="emg-add" onclick="App.Team.showEmergencyPicker('${pos}')">+ ${pos}</button>`;
                        html += `</div>`;
                    }
                }
                html += `</div>`;
            }

            grid.innerHTML = html;
        },

        async removeEmergency(playerId) {
            const slots = this._lastTeamData?.slots || [];
            const current = slots
                .filter(s => s.is_emergency && s.player_id !== playerId)
                .map(s => ({player_id: s.player_id, emergency_position: s.emergency_position || (s.position || '').split('/')[0].toUpperCase()}));
            await this._saveEmergenciesNew(current);
        },

        showEmergencyPicker(position) {
            if (!this._lastTeamData) return;
            const slots = this._lastTeamData.slots;
            const emergencies = slots.filter(s => s.is_emergency);
            const posCount = emergencies.filter(s => (s.emergency_position || '') === position).length;

            if (emergencies.length >= 4) { this._showToast('Maximum 4 emergencies', 'error'); return; }
            if (posCount >= 2) { this._showToast(`Already 2 ${position} emergencies`, 'error'); return; }

            // Find eligible bench players
            const bench = slots.filter(s => s.position_slot.startsWith('BENCH') && !s.is_emergency && !s.is_on_bye);
            const eligible = bench.filter(s => {
                const positions = (s.position || '').split('/').map(p => p.trim().toUpperCase());
                return positions.includes(position);
            });

            if (!eligible.length) { this._showToast(`No eligible ${position} bench players`, 'error'); return; }

            // Build picker dropdown
            const grid = document.getElementById('emergency-grid');
            const rect = grid.getBoundingClientRect();
            let picker = document.getElementById('emg-picker');
            if (picker) picker.remove();

            picker = document.createElement('div');
            picker.id = 'emg-picker';
            picker.className = 'emg-picker';
            picker.innerHTML = `<div class="emg-picker-header">Select ${position} Emergency</div>` +
                eligible.map(s => {
                    const name = this._abbreviateName(s.player_name);
                    return `<div class="emg-picker-item" onclick="App.Team.addEmergency(${s.player_id}, '${position}')">${this._esc(name)} <small>(${this._esc(s.position || '')})</small></div>`;
                }).join('') +
                `<div class="emg-picker-item emg-picker-cancel" onclick="document.getElementById('emg-picker').remove()">Cancel</div>`;
            document.body.appendChild(picker);

            // Position near the grid
            picker.style.position = 'fixed';
            picker.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - 200)}px`;
            picker.style.left = `${rect.left}px`;
        },

        async addEmergency(playerId, position) {
            const picker = document.getElementById('emg-picker');
            if (picker) picker.remove();

            const slots = this._lastTeamData?.slots || [];
            const current = slots
                .filter(s => s.is_emergency)
                .map(s => ({player_id: s.player_id, emergency_position: s.emergency_position || (s.position || '').split('/')[0].toUpperCase()}));
            current.push({player_id: playerId, emergency_position: position});
            await this._saveEmergenciesNew(current);
        },

        async _saveEmergenciesNew(emergencies) {
            try {
                const res = await authFetch(`${API_BASE}/api/team/emergency`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({emergencies}),
                });
                const data = await res.json();
                if (!res.ok) {
                    this._showToast(data.detail || 'Error saving emergencies', 'error');
                    return;
                }
                await this.loadTeam();
            } catch (e) {
                this._showToast('Error: ' + e.message, 'error');
            }
        },

        async autoSuggestEmergencies() {
            const round = App.state.config ? App.state.config.current_round : 1;
            try {
                const res = await authFetch(`${API_BASE}/api/team/emergency/suggest?round_num=${round}`);
                const data = await res.json();
                if (!data.suggestions || !data.suggestions.length) {
                    this._showToast('No suggestions available', 'error');
                    return;
                }
                const entries = data.suggestions.map(s => ({
                    player_id: s.player_id,
                    emergency_position: s.emergency_position,
                }));
                await this._saveEmergenciesNew(entries);
                this._showToast(`Set ${entries.length} suggested emergencies`, 'success');
            } catch (e) {
                this._showToast('Error: ' + e.message, 'error');
            }
        },

        async quickAddEmergency(playerId) {
            this.closeCardMenu();
            const slot = this._lastTeamData?.slots.find(s => s.player_id === playerId);
            if (!slot) return;
            const pos = (slot.position || '').split('/')[0].toUpperCase();
            await this.addEmergency(playerId, pos);
        },

        async quickRemoveEmergency(playerId) {
            this.closeCardMenu();
            await this.removeEmergency(playerId);
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
                this.loadByeImpact();
            } catch (e) {
                console.error('Failed to load team:', e);
            }
        },

        async loadByeImpact() {
            const round = App.state.config ? App.state.config.current_round : 1;
            const bar = document.getElementById('bye-impact-bar');
            try {
                const res = await authFetch(`${API_BASE}/api/analytics/bye-impact?round=${round}`);
                const data = await res.json();
                if (!data.has_byes || data.on_bye === 0) {
                    bar.style.display = 'none';
                    return;
                }
                bar.style.display = '';

                const total = data.playing + data.on_bye;
                const playPct = total > 0 ? (data.playing / total * 100) : 100;
                const byePct = total > 0 ? (data.on_bye / total * 100) : 0;

                document.getElementById('bye-bar-label').textContent = `Round ${data.round} Bye Impact`;
                document.getElementById('bye-bar-count').textContent =
                    `${data.playing}/${total} playing | ${data.on_bye} on BYE`;

                document.getElementById('bye-bar-fill-playing').style.width = `${playPct}%`;
                document.getElementById('bye-bar-fill-bye').style.width = `${byePct}%`;

                // Detail section
                let detail = '';
                if (data.bye_players && data.bye_players.length) {
                    detail += '<div class="bye-detail-section"><strong>On Bye:</strong> ';
                    detail += data.bye_players.map(p =>
                        `<span class="bye-player-tag">${this._esc(p.player_name)} <small>(${this._esc(p.position_slot)})</small></span>`
                    ).join(' ');
                    detail += '</div>';
                }
                if (data.available_bench && data.available_bench.length) {
                    detail += '<div class="bye-detail-section"><strong>Bench Available:</strong> ';
                    detail += data.available_bench.map(p =>
                        `<span class="bye-bench-tag">${this._esc(p.player_name)}${p.is_emergency ? ' (E)' : ''}</span>`
                    ).join(' ');
                    detail += '</div>';
                }
                if (data.coverage_gaps && data.coverage_gaps.length) {
                    detail += '<div class="bye-detail-section bye-gap-warning">';
                    detail += data.coverage_gaps.map(g => `<span>&#9888; ${this._esc(g)}</span>`).join(' ');
                    detail += '</div>';
                }
                const detailEl = document.getElementById('bye-bar-detail');
                detailEl.innerHTML = detail;
                detailEl.style.display = detail ? '' : 'none';
            } catch (e) {
                bar.style.display = 'none';
                console.error('Failed to load bye impact:', e);
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
            this.renderEmergencyPanel();
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

            // Render fixtures if already loaded (widget is in sidebar)
            if (this._fixtureData) {
                this._renderFixtureWidget();
            }
        },

        _renderFieldCard(s) {
            const teamColor = TEAM_COLORS[s.team] || '#444';
            const teamAbbr = TEAM_ABBREVS[s.team] || (s.team || '').substring(0, 3).toUpperCase();
            const salary = s.salary ? `$${(s.salary / 1000).toFixed(0)}k` : '';
            const score = this._getDisplayScore(s);
            const displayName = this._abbreviateName(s.player_name);

            const selectable = this._captainMode ? ' captain-selectable' : '';

            // Card classes for captain/vc styling + team guernsey
            const teamSlug = (s.team || '').toLowerCase().replace(/\s+/g, '-');
            let cardClass = `field-card team-${teamSlug}${selectable}`;
            if (s.is_captain) cardClass += ' is-captain';
            if (s.is_vice_captain) cardClass += ' is-vc';
            if (s.is_on_bye) cardClass += ' is-bye';

            let html = `<div class="${cardClass}" data-pid="${s.player_id}" data-slot="${s.position_slot}" style="--team-color:${teamColor}" onclick="App.Team.handleCardClick(${s.player_id})" oncontextmenu="App.Team.showCardMenu(event, ${s.id}, ${s.player_id})">`;

            // Remove button
            html += `<button class="fc-remove" onclick="event.stopPropagation();App.Team.removePlayer(${s.id})" title="Remove">&times;</button>`;

            // Top row: name + role badge inline
            html += '<div class="fc-top">';
            html += `<div class="fc-name">${this._esc(displayName)}</div>`;
            if (s.is_captain) html += '<span class="fc-role fc-role-c">C</span>';
            else if (s.is_vice_captain) html += '<span class="fc-role fc-role-vc">VC</span>';
            html += '</div>';

            // Bottom: team dot + team abbr + score + salary
            html += '<div class="fc-meta">';
            html += `<span class="fc-team-dot" style="background:${teamColor}"></span>`;
            html += `<span class="fc-team">${this._esc(teamAbbr)}</span>`;
            html += `<span class="fc-score">${score}</span>`;
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
            let clickHandler = '';

            if (this._emergencyMode) {
                selectable = ' emg-selectable';
                clickHandler = `onclick="App.Team.handleBenchEmergencyClick(${s.player_id})"`;
            } else {
                clickHandler = `onclick="App.Team.handleCardClick(${s.player_id})"`;
            }

            const teamSlug = (s.team || '').toLowerCase().replace(/\s+/g, '-');
            const byeClass = s.is_on_bye ? ' is-bye' : '';
            let html = `<div class="bench-card team-${teamSlug}${selectable}${byeClass}" data-pid="${s.player_id}" data-slot="${s.position_slot}" style="--team-color:${teamColor}" oncontextmenu="App.Team.showCardMenu(event, ${s.id}, ${s.player_id})" ${clickHandler}>`;

            html += `<button class="fc-remove" onclick="event.stopPropagation();App.Team.removePlayer(${s.id})" title="Remove">&times;</button>`;

            // Emergency badge
            const emgIdx = this._emergencyMode
                ? this._emergencyPicks.indexOf(s.player_id)
                : -1;
            if (emgIdx !== -1) {
                html += `<div class="fc-emg">E${emgIdx + 1}</div>`;
            } else if (!this._emergencyMode && s.is_emergency && s.emergency_order) {
                html += `<div class="fc-emg">E${s.emergency_order}</div>`;
            }

            // Top row: name + role
            html += '<div class="fc-top">';
            html += `<div class="fc-name">${this._esc(displayName)}</div>`;
            if (s.is_captain) html += '<span class="fc-role fc-role-c">C</span>';
            else if (s.is_vice_captain) html += '<span class="fc-role fc-role-vc">VC</span>';
            html += '</div>';

            html += '<div class="fc-meta">';
            html += `<span class="fc-team-dot" style="background:${teamColor}"></span>`;
            html += `<span class="fc-team">${this._esc(teamAbbr)}</span>`;
            html += `<span class="fc-score">${score}</span>`;
            html += `<span class="fc-salary">${salary}</span>`;
            html += '</div>';

            // Lineup status
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

                // Bye impact
                if (rec.bye_impact) {
                    const bi = rec.bye_impact;
                    let byeHtml = '';
                    if (bi.net_field_change > 0) {
                        byeHtml = `<span style="color:var(--accent-green)">+${bi.net_field_change} field player${bi.net_field_change > 1 ? 's' : ''} in R${bi.rounds_gained.join(', R')}</span>`;
                    } else if (bi.net_field_change < 0) {
                        byeHtml = `<span style="color:var(--accent-red)">${bi.net_field_change} field player in R${bi.rounds_lost.join(', R')}</span>`;
                    } else if (bi.rounds_gained.length > 0) {
                        byeHtml = `<span style="color:var(--text-secondary)">Bye swap: gain R${bi.rounds_gained.join(',')} / lose R${bi.rounds_lost.join(',')}</span>`;
                    } else {
                        byeHtml = `<span style="color:var(--text-muted)">No bye change</span>`;
                    }
                    html += `<div class="trade-stats" style="font-size:11px">Bye impact: ${byeHtml}</div>`;
                }

                html += `</div>`;
            });

            container.innerHTML = html;
        },

        renderInjuries(data) {
            document.getElementById('stat-injuries').textContent = data.team_injuries.length;

            const container = document.getElementById('injuries-display');
            let html = '';

            // My team injuries
            if (data.team_injuries.length) {
                html += '<h4 style="margin:0 0 8px;font-size:13px;color:#64748b">My Team</h4>';
                data.team_injuries.forEach(inj => {
                    html += `<div class="injury-alert" style="display:block;margin-bottom:8px;padding:10px">`;
                    html += `<strong>${esc(inj.player_name)}</strong> (${esc(inj.team)}) — `;
                    html += `${esc(inj.injury_type || 'Unknown')} | Return: ${esc(inj.estimated_return || 'TBC')}`;
                    html += `</div>`;
                });
            } else {
                html += '<div class="empty-state" style="color:var(--accent-green);margin-bottom:12px">No injuries on your team!</div>';
            }

            // All league injuries (collapsible, scrollable)
            if (data.all_injuries && data.all_injuries.length) {
                html += `<details style="margin-top:12px">`;
                html += `<summary style="cursor:pointer;font-size:13px;color:#64748b;font-weight:600;margin-bottom:8px">All League Injuries (${data.all_injuries.length})</summary>`;
                html += `<div style="max-height:400px;overflow-y:auto;padding-right:4px">`;
                data.all_injuries.forEach(inj => {
                    html += `<div class="injury-alert" style="display:block;margin-bottom:6px;padding:8px;font-size:12px">`;
                    html += `<strong>${esc(inj.player_name)}</strong> (${esc(inj.team)}) — `;
                    html += `${esc(inj.injury_type || 'Unknown')} | Return: ${esc(inj.estimated_return || 'TBC')}`;
                    html += `</div>`;
                });
                html += `</div></details>`;
            }

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

    // --- Bye Round Planner ---
    Byes: {
        _data: null,

        async loadAll() {
            try {
                const res = await authFetch(`${API_BASE}/api/analytics/bye-planner`);
                const data = await res.json();
                this._data = data;
                this.render(data);
            } catch (e) {
                console.error('Failed to load bye planner:', e);
                document.getElementById('bye-risk-summary').innerHTML =
                    '<div class="empty-state">Failed to load bye data</div>';
            }
        },

        render(data) {
            this.renderRiskScore(data.bye_risk_score, data.bye_rounds);
            this.renderMatrix(data.players, data.bye_rounds);
            this.renderRoundSummaries(data.round_summaries, data.bye_rounds);
        },

        renderRiskScore(score, byeRounds) {
            const container = document.getElementById('bye-risk-summary');
            if (!byeRounds || !byeRounds.length) {
                container.innerHTML = '<div class="empty-state">No bye rounds found. Sync fixture data first.</div>';
                return;
            }

            const color = score >= 70 ? 'var(--accent-green)' :
                          score >= 40 ? 'var(--accent-yellow)' : 'var(--accent-red)';
            const label = score >= 70 ? 'Well Prepared' :
                          score >= 40 ? 'Some Risk' : 'High Risk';

            let html = '<div class="bye-risk-card">';
            html += '<div class="bye-risk-gauge">';
            html += `<div class="bye-risk-score" style="color:${color}">${score}</div>`;
            html += `<div class="bye-risk-label">${esc(label)}</div>`;
            html += '<div class="bye-risk-sublabel">Bye Readiness Score</div>';
            html += '</div>';
            html += `<div class="bye-risk-rounds">Bye rounds: ${byeRounds.join(', ')}</div>`;
            html += '</div>';
            container.innerHTML = html;
        },

        renderMatrix(players, byeRounds) {
            const container = document.getElementById('bye-matrix');
            if (!players || !players.length || !byeRounds || !byeRounds.length) {
                container.innerHTML = '<div class="empty-state">Import your team to see bye coverage matrix</div>';
                return;
            }

            // Separate on-field vs bench
            const onField = players.filter(p => p.is_on_field);
            const bench = players.filter(p => !p.is_on_field);

            let html = '<div class="bye-matrix-scroll"><table class="bye-matrix-table">';

            // Header row
            html += '<thead><tr>';
            html += '<th class="bye-matrix-sticky">Player</th>';
            html += '<th>Team</th>';
            html += '<th>Pos</th>';
            for (const rnd of byeRounds) {
                html += `<th class="bye-matrix-rnd">R${rnd}</th>`;
            }
            html += '</tr></thead><tbody>';

            // Render grouped rows
            const renderGroup = (group, label) => {
                html += `<tr class="bye-matrix-group"><td colspan="${3 + byeRounds.length}">${esc(label)}</td></tr>`;
                for (const p of group) {
                    html += '<tr>';
                    html += `<td class="bye-matrix-sticky bye-matrix-name">${esc(p.player_name)}</td>`;
                    html += `<td class="bye-matrix-team">${esc(p.team)}</td>`;
                    html += `<td class="bye-matrix-pos">${esc(p.position || '-')}</td>`;
                    for (const rnd of byeRounds) {
                        const status = p.round_status[String(rnd)] || 'playing';
                        const cls = status === 'bye' ? 'bye-cell-bye' :
                                    status === 'injured' ? 'bye-cell-injured' : 'bye-cell-playing';
                        const icon = status === 'bye' ? 'BYE' :
                                     status === 'injured' ? '&#9888;' : '&#10003;';
                        html += `<td class="bye-matrix-cell ${cls}">${icon}</td>`;
                    }
                    html += '</tr>';
                }
            };

            renderGroup(onField, 'On Field');
            renderGroup(bench, 'Bench');

            html += '</tbody></table></div>';
            container.innerHTML = html;
        },

        renderRoundSummaries(summaries, byeRounds) {
            const container = document.getElementById('bye-round-summaries');
            if (!byeRounds || !byeRounds.length) {
                container.innerHTML = '';
                return;
            }

            let html = '<div class="bye-summary-cards">';
            for (const rnd of byeRounds) {
                const s = summaries[String(rnd)];
                if (!s) continue;

                const statusClass = s.danger ? 'danger' : s.warning ? 'warning' : 'ok';
                const statusLabel = s.danger ? 'DANGER' : s.warning ? 'WARNING' : 'OK';

                html += `<div class="bye-summary-card ${statusClass}">`;
                html += `<div class="bye-summary-round">Round ${rnd}</div>`;
                html += `<div class="bye-summary-count">${s.playing}<small>/${s.playing + s.on_bye}</small></div>`;
                html += `<div class="bye-summary-label">playing</div>`;
                html += `<div class="bye-summary-badge ${statusClass}">${statusLabel}</div>`;
                if (s.coverage_gaps && s.coverage_gaps.length) {
                    html += '<div class="bye-summary-gaps">';
                    s.coverage_gaps.forEach(g => { html += `<small>${esc(g)}</small>`; });
                    html += '</div>';
                }
                html += '</div>';
            }
            html += '</div>';
            container.innerHTML = html;
        },
    },

    // --- Trade War Room ---
    WarRoom: {
        _data: null,
        _chatHistory: [],

        async loadAll() {
            const round = App.state.config ? App.state.config.current_round : 1;
            try {
                const res = await authFetch(`${API_BASE}/api/analytics/trade-warroom?round=${round}`);
                this._data = await res.json();
                this.renderStatusBar(this._data);
                this.renderProblems(this._data.problems);
                this.renderHistory(this._data.trade_history);
            } catch (e) {
                console.error('War room load failed:', e);
                document.getElementById('warroom-status').innerHTML =
                    '<div class="empty-state">Failed to load trade data</div>';
            }
        },

        renderStatusBar(data) {
            const container = document.getElementById('warroom-status');
            container.innerHTML = `
                <div class="wr-stat"><span class="wr-stat-label">Trades</span><span class="wr-stat-value">${data.trades_remaining}/${data.total_trades}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Boosts</span><span class="wr-stat-value">${data.boosts_remaining}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Budget</span><span class="wr-stat-value">$${(data.budget_remaining || 0).toLocaleString()}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Round</span><span class="wr-stat-value">${data.round}</span></div>
            `;
        },

        renderProblems(problems) {
            const container = document.getElementById('warroom-problems');
            if (!problems || !problems.length) {
                container.innerHTML = '<h3>Team Scan</h3><div class="wr-no-problems">No urgent problems detected. Your team looks healthy.</div>';
                return;
            }
            const emoji = {critical: 'RED', warning: 'YEL', info: 'INFO'};
            let html = `<h3>Problems Detected (${problems.length})</h3><div class="wr-problems-list">`;
            for (const p of problems) {
                html += `<div class="wr-problem severity-${p.severity}">`;
                html += `<div class="wr-problem-header"><span>${esc(p.name)}</span><span class="wr-problem-price">$${(p.price || 0).toLocaleString()}</span></div>`;
                html += `<div class="wr-problem-detail">${esc(p.detail)}</div>`;
                html += `<div class="wr-problem-rec">${esc(p.recommendation)}</div>`;
                html += `</div>`;
            }
            html += '</div>';
            container.innerHTML = html;
        },

        async generateRecommendations() {
            const btn = document.getElementById('warroom-generate-btn');
            const container = document.getElementById('warroom-recommendations');
            btn.disabled = true;
            btn.textContent = 'Analysing...';
            container.innerHTML = '<div class="loading"><div class="spinner"></div><div>AI is analysing your team...</div></div>';

            const round = App.state.config ? App.state.config.current_round : 1;
            try {
                const res = await authFetch(`${API_BASE}/api/ai/trade-warroom`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({round, season: 2026}),
                });
                const data = await res.json();
                container.innerHTML = `<div class="wr-ai-response">${renderMarkdown(data.recommendations)}</div>`;
            } catch (e) {
                container.innerHTML = `<div class="empty-state" style="color:var(--accent-red)">Failed: ${e.message}</div>`;
            }
            btn.disabled = false;
            btn.textContent = 'Analyse My Team';
        },

        async askQuestion() {
            const input = document.getElementById('warroom-chat-input');
            const question = input.value.trim();
            if (!question) return;

            const log = document.getElementById('warroom-chat-log');
            log.innerHTML += `<div class="wr-chat-msg wr-chat-user">${esc(question)}</div>`;
            log.innerHTML += `<div class="wr-chat-msg wr-chat-ai" id="wr-chat-pending"><div class="loading" style="padding:8px"><div class="spinner"></div></div></div>`;
            input.value = '';
            log.scrollTop = log.scrollHeight;

            const round = App.state.config ? App.state.config.current_round : 1;
            try {
                const res = await authFetch(`${API_BASE}/api/ai/trade-chat`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        round, season: 2026, question,
                        history: this._chatHistory,
                    }),
                });
                const data = await res.json();
                const pending = document.getElementById('wr-chat-pending');
                if (pending) {
                    pending.id = '';
                    pending.innerHTML = renderMarkdown(data.answer);
                }
                this._chatHistory.push({role: 'user', content: question});
                this._chatHistory.push({role: 'assistant', content: data.answer});
            } catch (e) {
                const pending = document.getElementById('wr-chat-pending');
                if (pending) {
                    pending.id = '';
                    pending.innerHTML = `<span style="color:var(--accent-red)">Error: ${e.message}</span>`;
                }
            }
            log.scrollTop = log.scrollHeight;
        },

        renderHistory(history) {
            const container = document.getElementById('warroom-history');
            if (!history || !history.length) {
                container.innerHTML = '<div class="empty-state">No trades yet this season</div>';
                return;
            }
            let html = '<table class="data-table"><thead><tr><th>Round</th><th>Out</th><th>In</th><th class="right">Price Diff</th></tr></thead><tbody>';
            for (const t of history) {
                const diff = (t.price_in || 0) - (t.price_out || 0);
                const cls = diff > 0 ? 'style="color:var(--accent-red)"' : diff < 0 ? 'style="color:var(--accent-green)"' : '';
                html += `<tr><td>R${t.round}</td><td>${esc(t.player_out_name)}</td><td>${esc(t.player_in_name)}</td>`;
                html += `<td class="right" ${cls}>${diff >= 0 ? '+' : ''}$${diff.toLocaleString()}</td></tr>`;
            }
            html += '</tbody></table>';
            container.innerHTML = html;
        },
    },

    // --- Weekly Briefing ---
    Briefing: {
        async load() {
            const content = document.getElementById('briefing-content');
            const ts = document.getElementById('briefing-timestamp');
            const round = App.state.config ? App.state.config.current_round : 1;

            try {
                const res = await authFetch(`${API_BASE}/api/ai/weekly-briefing?round_num=${round}`);
                const data = await res.json();
                if (data.exists) {
                    content.innerHTML = `<div class="briefing-rendered">${renderMarkdown(data.briefing)}</div>`;
                    ts.textContent = data.generated_at ? `Generated: ${new Date(data.generated_at).toLocaleDateString('en-AU', {weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'})}` : '';
                } else {
                    content.innerHTML = `
                        <div class="empty-state" style="padding:30px">
                            <p>No briefing for Round ${round} yet.</p>
                            <button class="btn btn-primary btn-optimise" onclick="App.Briefing.generate(false)" style="margin-top:12px">Generate Briefing</button>
                            <p style="font-size:11px;color:var(--text-muted);margin-top:8px">Briefings auto-generate on Thursday evenings after team selections.</p>
                        </div>`;
                    ts.textContent = '';
                }
            } catch (e) {
                content.innerHTML = '<div class="empty-state">Failed to load briefing</div>';
            }
        },

        async generate(force) {
            const content = document.getElementById('briefing-content');
            const btn = document.getElementById('briefing-regen-btn');
            const round = App.state.config ? App.state.config.current_round : 1;

            content.innerHTML = '<div class="loading"><div class="spinner"></div><div>AI is analysing your team, fixtures, injuries, and matchups...</div></div>';
            if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }

            try {
                const res = await authFetch(`${API_BASE}/api/ai/weekly-briefing`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({round, season: 2026, force}),
                });
                const data = await res.json();
                content.innerHTML = `<div class="briefing-rendered">${renderMarkdown(data.briefing)}</div>`;
                document.getElementById('briefing-timestamp').textContent = data.cached ? '' : 'Generated: just now';
            } catch (e) {
                content.innerHTML = `<div class="empty-state" style="color:var(--accent-red)">Failed: ${e.message}</div>`;
            }

            if (btn) { btn.disabled = false; btn.textContent = 'Regenerate'; }
        },
    },

    // --- Ownership Intelligence ---
    Ownership: {
        async loadAll() {
            const round = App.state.config ? App.state.config.current_round : 1;
            await Promise.allSettled([
                this.loadMovers(round),
                this.loadTemplate(),
            ]);
        },

        async loadMovers(round) {
            try {
                const res = await authFetch(`${API_BASE}/api/analytics/ownership/movers?round=${round}`);
                const data = await res.json();
                this.renderMovers(data);
            } catch (e) { console.error('Movers failed:', e); }
        },

        renderMovers(data) {
            const inEl = document.getElementById('own-movers-in');
            const outEl = document.getElementById('own-movers-out');
            if (data.gainers && data.gainers.length) {
                inEl.innerHTML = data.gainers.map(g =>
                    `<div class="own-mover-row own-mover-up"><span>${esc(g.player_name)}</span><span class="own-mover-chg">+${g.ownership_change.toFixed(1)}%</span></div>`
                ).join('');
            } else {
                inEl.innerHTML = '<div class="empty-state">No data yet</div>';
            }
            if (data.losers && data.losers.length) {
                outEl.innerHTML = data.losers.map(l =>
                    `<div class="own-mover-row own-mover-down"><span>${esc(l.player_name)}</span><span class="own-mover-chg">${l.ownership_change.toFixed(1)}%</span></div>`
                ).join('');
            } else {
                outEl.innerHTML = '<div class="empty-state">No data yet</div>';
            }
        },

        async loadTemplate() {
            try {
                const res = await authFetch(`${API_BASE}/api/analytics/ownership/template`);
                const data = await res.json();
                const el = document.getElementById('own-template');
                if (data.template_players && data.template_players.length) {
                    el.innerHTML = data.template_players.map(p =>
                        `<div class="own-template-row">
                            <span class="own-template-name">${esc(p.player_name)} <small style="color:var(--text-muted)">(${esc(p.team)}, ${esc(p.position || '')})</small></span>
                            <span class="own-template-pct">${p.ownership_pct.toFixed(0)}%</span>
                            <div class="own-template-bar"><div class="own-template-fill" style="width:${Math.min(p.ownership_pct, 100)}%"></div></div>
                        </div>`
                    ).join('');
                } else {
                    el.innerHTML = '<div class="empty-state">No ownership data available yet</div>';
                }
            } catch (e) { console.error('Template failed:', e); }
        },

        async findPODs() {
            const minAvg = document.getElementById('pod-min-avg').value;
            const maxOwn = document.getElementById('pod-max-own').value;
            const position = document.getElementById('pod-position').value;
            const container = document.getElementById('own-pod-results');
            container.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

            try {
                const res = await authFetch(
                    `${API_BASE}/api/analytics/ownership/pods?min_avg=${minAvg}&max_ownership=${maxOwn}&position=${position}`
                );
                const data = await res.json();
                if (!data.pods || !data.pods.length) {
                    container.innerHTML = '<div class="empty-state">No PODs found. Try relaxing the filters.</div>';
                    return;
                }
                let html = '<table class="data-table"><thead><tr><th>Player</th><th>Team</th><th>Pos</th><th class="right">Avg</th><th class="right">Own%</th></tr></thead><tbody>';
                for (const p of data.pods) {
                    html += `<tr><td>${esc(p.player_name)}</td><td class="muted">${esc(p.team)}</td><td class="muted">${esc(p.position || '-')}</td>`;
                    html += `<td class="right" style="font-weight:700">${p.avg_score.toFixed(0)}</td>`;
                    html += `<td class="right">${p.ownership_pct ? p.ownership_pct.toFixed(0) + '%' : '<5%'}</td></tr>`;
                }
                html += '</tbody></table>';
                container.innerHTML = html;
            } catch (e) {
                container.innerHTML = `<div class="empty-state" style="color:var(--accent-red)">Error: ${e.message}</div>`;
            }
        },
    },

    // --- Season Tracker ---
    Tracker: {
        _data: null,
        _chartMode: 'cumulative',

        async loadAll() {
            try {
                const res = await authFetch(`${API_BASE}/api/analytics/season-tracker`);
                this._data = await res.json();
                this.renderSummary(this._data.summary);
                this.renderChart(this._data.round_scores);
                this.renderCaptain(this._data.captain);
                this.renderTrades(this._data.trades);
                this.renderRating(this._data);
            } catch (e) {
                console.error('Tracker load failed:', e);
            }
        },

        renderSummary(s) {
            document.getElementById('tracker-summary').innerHTML = `
                <div class="wr-stat"><span class="wr-stat-label">Total</span><span class="wr-stat-value">${(s.total_score || 0).toLocaleString()}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Avg/Round</span><span class="wr-stat-value">${s.average_score || '-'}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Best</span><span class="wr-stat-value">${s.best_round ? `${s.best_round.score.toLocaleString()} (R${s.best_round.round})` : '-'}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Rounds</span><span class="wr-stat-value">${s.rounds_played}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Trades Left</span><span class="wr-stat-value">${s.trades_remaining}/30</span></div>
            `;
        },

        setChartMode(mode) {
            this._chartMode = mode;
            document.getElementById('chart-btn-cumulative').classList.toggle('active', mode === 'cumulative');
            document.getElementById('chart-btn-round').classList.toggle('active', mode === 'round');
            if (this._data) this.renderChart(this._data.round_scores);
        },

        renderChart(roundScores) {
            const canvas = document.getElementById('tracker-chart');
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            const dpr = window.devicePixelRatio || 1;
            const rect = canvas.parentElement.getBoundingClientRect();
            canvas.width = rect.width * dpr;
            canvas.height = 260 * dpr;
            canvas.style.width = rect.width + 'px';
            canvas.style.height = '260px';
            ctx.scale(dpr, dpr);

            const w = rect.width, h = 260;
            const pad = {top: 20, right: 20, bottom: 35, left: 55};
            const cw = w - pad.left - pad.right, ch = h - pad.top - pad.bottom;

            ctx.clearRect(0, 0, w, h);

            if (!roundScores || !roundScores.length) {
                ctx.fillStyle = '#94a3b8'; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
                ctx.fillText('No round data yet', w / 2, h / 2);
                return;
            }

            let data;
            if (this._chartMode === 'cumulative') {
                let cum = 0;
                data = roundScores.map(r => { cum += r.score || 0; return cum; });
            } else {
                data = roundScores.map(r => r.score || 0);
            }

            const maxVal = Math.max(...data) * 1.1 || 100;
            const minVal = this._chartMode === 'round' ? Math.min(...data) * 0.9 : 0;
            const xStep = cw / Math.max(data.length - 1, 1);

            // Grid
            ctx.strokeStyle = 'rgba(255,255,255,0.06)'; ctx.lineWidth = 1;
            for (let i = 0; i <= 4; i++) {
                const y = pad.top + (ch / 4) * i;
                ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
                const val = maxVal - ((maxVal - minVal) / 4) * i;
                ctx.fillStyle = '#64748b'; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
                ctx.fillText(Math.round(val).toLocaleString(), pad.left - 6, y + 3);
            }

            // Line
            ctx.beginPath(); ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 2.5; ctx.lineJoin = 'round';
            data.forEach((val, i) => {
                const x = pad.left + i * xStep;
                const y = pad.top + ch - ((val - minVal) / (maxVal - minVal)) * ch;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            });
            ctx.stroke();

            // Fill
            ctx.lineTo(pad.left + (data.length - 1) * xStep, pad.top + ch);
            ctx.lineTo(pad.left, pad.top + ch);
            ctx.closePath();
            ctx.fillStyle = 'rgba(99,102,241,0.08)'; ctx.fill();

            // Points + X labels
            ctx.fillStyle = '#64748b'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
            data.forEach((val, i) => {
                const x = pad.left + i * xStep;
                const y = pad.top + ch - ((val - minVal) / (maxVal - minVal)) * ch;
                ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2);
                ctx.fillStyle = '#6366f1'; ctx.fill();
                ctx.fillStyle = '#64748b';
                ctx.fillText(`R${roundScores[i].round}`, x, h - 10);
            });
        },

        renderCaptain(c) {
            const statsEl = document.getElementById('tracker-captain-stats');
            const histEl = document.getElementById('tracker-captain-history');

            statsEl.innerHTML = `
                <div class="wr-stat"><span class="wr-stat-label">Hit Rate</span><span class="wr-stat-value" style="color:${c.hit_rate >= 50 ? 'var(--accent-green)' : 'var(--accent-red)'}">${c.hit_rate}%</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Correct</span><span class="wr-stat-value">${c.correct}/${c.total}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Pts Left on Table</span><span class="wr-stat-value" style="color:var(--accent-red)">${c.points_left_on_table || 0}</span></div>
            `;

            if (!c.history || !c.history.length) { histEl.innerHTML = '<div class="empty-state">No captain data yet</div>'; return; }

            let html = '<table class="data-table"><thead><tr><th>Rd</th><th>Captain</th><th>Score</th><th>Doubled</th><th>Best</th><th>Best Score</th><th></th></tr></thead><tbody>';
            for (const r of c.history) {
                const icon = r.was_correct ? '&#10003;' : '&#10005;';
                const cls = r.was_correct ? '' : 'style="background:rgba(239,68,68,0.06)"';
                html += `<tr ${cls}><td>R${r.round}</td><td><strong>${esc(r.picked || '-')}</strong></td><td>${r.picked_score || '-'}</td><td>${r.picked_doubled || '-'}</td>`;
                html += `<td>${r.was_correct ? '-' : esc(r.optimal || '-')}</td><td>${r.was_correct ? '-' : r.optimal_score || '-'}</td><td>${icon}</td></tr>`;
            }
            html += '</tbody></table>';
            histEl.innerHTML = html;
        },

        renderTrades(t) {
            const statsEl = document.getElementById('tracker-trade-stats');
            const ledgerEl = document.getElementById('tracker-trade-ledger');

            statsEl.innerHTML = `
                <div class="wr-stat"><span class="wr-stat-label">Wins</span><span class="wr-stat-value" style="color:var(--accent-green)">${t.won}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Losses</span><span class="wr-stat-value" style="color:var(--accent-red)">${t.lost}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Pending</span><span class="wr-stat-value" style="color:var(--text-muted)">${t.pending}</span></div>
                <div class="wr-stat"><span class="wr-stat-label">Used</span><span class="wr-stat-value">${t.total}/30</span></div>
            `;

            if (!t.ledger || !t.ledger.length) { ledgerEl.innerHTML = '<div class="empty-state">No trades yet</div>'; return; }

            const verdictMap = {win: '&#10003; Win', loss: '&#10005; Loss', even: '- Even', too_early: '... Pending'};
            let html = '<table class="data-table"><thead><tr><th>Rd</th><th>Out</th><th>In</th><th class="right">Avg Since</th><th>Verdict</th></tr></thead><tbody>';
            for (const tr of t.ledger) {
                const vc = tr.verdict === 'win' ? 'color:var(--accent-green)' : tr.verdict === 'loss' ? 'color:var(--accent-red)' : '';
                html += `<tr><td>R${tr.round}${tr.was_boost ? ' &#9889;' : ''}</td><td>${esc(tr.out_name)}</td><td><strong>${esc(tr.in_name)}</strong></td>`;
                html += `<td class="right">${tr.games_since >= 2 ? `${tr.in_avg_since} vs ${tr.out_avg_since}` : '-'}</td>`;
                html += `<td style="${vc}">${verdictMap[tr.verdict] || '?'}</td></tr>`;
            }
            html += '</tbody></table>';
            ledgerEl.innerHTML = html;
        },

        renderRating(data) {
            const el = document.getElementById('tracker-coach-rating');
            if (!data.round_scores || !data.round_scores.length) { el.innerHTML = '<div class="empty-state">Rating appears after a few rounds</div>'; return; }

            const capScore = (data.captain.hit_rate || 0) * 0.3;
            const tradeScore = data.trades.total > 0 ? ((data.trades.won / Math.max(data.trades.won + data.trades.lost, 1)) * 100) * 0.3 : 50 * 0.3;
            const scores = data.round_scores.map(r => r.score || 0);
            const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
            const stdDev = Math.sqrt(scores.reduce((s, v) => s + Math.pow(v - mean, 2), 0) / scores.length);
            const consistency = Math.max(0, Math.min(100, 100 - stdDev / 5)) * 0.2;
            const fullField = data.round_scores.filter(r => (r.field_players || 0) >= 22).length;
            const fieldRate = (fullField / data.round_scores.length * 100) * 0.2;
            const total = Math.round(capScore + tradeScore + consistency + fieldRate);

            const cls = total >= 80 ? 'var(--accent-green)' : total >= 60 ? 'var(--accent-blue)' : total >= 40 ? 'var(--accent-yellow)' : 'var(--accent-red)';
            const label = total >= 80 ? 'Elite' : total >= 60 ? 'Strong' : total >= 40 ? 'Average' : 'Needs Work';

            el.innerHTML = `
                <div style="text-align:center;margin-bottom:20px">
                    <div style="display:inline-flex;flex-direction:column;align-items:center;justify-content:center;width:100px;height:100px;border-radius:50%;border:4px solid ${cls}">
                        <span style="font-size:32px;font-weight:800;color:${cls}">${total}</span>
                        <span style="font-size:11px;color:var(--text-muted)">/100</span>
                    </div>
                    <div style="font-size:15px;font-weight:700;margin-top:6px;color:${cls}">${label}</div>
                </div>
                <div style="max-width:350px;margin:0 auto">
                    <div class="tracker-rating-row"><span>Captain Picks</span><div class="tracker-rating-bar"><div style="width:${data.captain.hit_rate}%;background:${cls};height:100%;border-radius:3px"></div></div><span>${Math.round(data.captain.hit_rate)}%</span></div>
                    <div class="tracker-rating-row"><span>Trade Success</span><div class="tracker-rating-bar"><div style="width:${data.trades.total > 0 ? (data.trades.won / Math.max(data.trades.won + data.trades.lost, 1)) * 100 : 50}%;background:${cls};height:100%;border-radius:3px"></div></div><span>${data.trades.won}W/${data.trades.lost}L</span></div>
                    <div class="tracker-rating-row"><span>Consistency</span><div class="tracker-rating-bar"><div style="width:${consistency / 0.2}%;background:${cls};height:100%;border-radius:3px"></div></div><span>${Math.round(consistency / 0.2)}%</span></div>
                    <div class="tracker-rating-row"><span>Field Coverage</span><div class="tracker-rating-bar"><div style="width:${fieldRate / 0.2}%;background:${cls};height:100%;border-radius:3px"></div></div><span>${Math.round(fieldRate / 0.2)}%</span></div>
                </div>
            `;
        },
    },

    // --- Player Comparison ---
    Compare: {
        _ids: [null, null],
        _timeout: null,

        search(query, slot) {
            const dropdown = document.getElementById(`cmp-dropdown-${slot}`);
            if (!query || query.length < 2) { dropdown.style.display = 'none'; return; }

            clearTimeout(this._timeout);
            this._timeout = setTimeout(async () => {
                try {
                    const res = await authFetch(`${API_BASE}/api/players/search?q=${encodeURIComponent(query)}&limit=8`);
                    const data = await res.json();
                    if (data.players && data.players.length) {
                        dropdown.innerHTML = data.players.map(p =>
                            `<div class="cmp-opt" onclick="App.Compare.select(${slot}, ${p.id}, '${p.name.replace(/'/g, "\\'")}')">${esc(p.name)} <small style="color:var(--text-muted)">${esc(p.team)} ${esc(p.position || '')}</small></div>`
                        ).join('');
                        dropdown.style.display = 'block';
                    } else {
                        dropdown.innerHTML = '<div class="cmp-opt" style="color:var(--text-muted)">No results</div>';
                        dropdown.style.display = 'block';
                    }
                } catch (e) { dropdown.style.display = 'none'; }
            }, 250);
        },

        select(slot, id, name) {
            this._ids[slot] = id;
            document.getElementById(`cmp-search-${slot + 1}`).value = name;
            document.getElementById(`cmp-dropdown-${slot}`).style.display = 'none';
            if (this._ids[0] && this._ids[1]) this.run();
        },

        async run() {
            const ids = this._ids.filter(Boolean);
            if (ids.length < 2) return;
            const container = document.getElementById('cmp-results');
            container.innerHTML = '<div class="loading"><div class="spinner"></div><div>Comparing...</div></div>';

            try {
                const res = await authFetch(`${API_BASE}/api/players/compare?ids=${ids.join(',')}`);
                const data = await res.json();
                this.render(data.players);
                this.loadVerdict(ids);
            } catch (e) {
                container.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
            }
        },

        render(players) {
            if (!players || players.length < 2) return;
            const container = document.getElementById('cmp-results');

            const hi = (vals, higher = true) => {
                const nums = vals.map(v => typeof v === 'number' ? v : -Infinity);
                const best = higher ? Math.max(...nums) : Math.min(...nums);
                return vals.map(v => {
                    const fmt = v === null || v === undefined ? '-' : typeof v === 'number' && v > 1000 ? '$' + v.toLocaleString() : v;
                    return v === best && v !== null && typeof v === 'number' ? `<td class="cmp-cell cmp-best">${fmt}</td>` : `<td class="cmp-cell">${fmt}</td>`;
                }).join('');
            };

            const spark = (scores) => {
                if (!scores || !scores.length) return '-';
                const mx = Math.max(...scores), mn = Math.min(...scores), rng = mx - mn || 1;
                const blocks = ['▁','▂','▃','▄','▅','▆','▇','█'];
                return '<span style="font-family:monospace;color:var(--accent-blue);letter-spacing:-1px">' +
                    scores.map(s => blocks[Math.round(((s - mn) / rng) * 7)]).join('') + '</span>';
            };

            let html = '<table class="data-table" style="font-size:12px"><thead><tr><th></th>';
            players.forEach(p => {
                html += `<th style="text-align:center;padding:12px"><div style="font-size:14px;font-weight:700">${esc(p.name)}</div><div style="font-size:11px;color:var(--text-muted)">${esc(p.team)} · ${esc(p.position || '')}</div><div style="color:var(--accent-cyan);font-weight:600">$${(p.price || 0).toLocaleString()}</div></th>`;
            });
            html += '</tr></thead><tbody>';

            // Scoring
            html += `<tr style="background:var(--bg-card)"><td colspan="${players.length + 1}" style="font-weight:700;color:var(--accent-cyan)">Scoring</td></tr>`;
            html += `<tr><td class="muted">Season Avg</td>${hi(players.map(p => p.scoring.season_avg))}</tr>`;
            html += `<tr><td class="muted">Last 3</td>${hi(players.map(p => p.scoring.last_3_avg))}</tr>`;
            html += `<tr><td class="muted">Last 5</td>${hi(players.map(p => p.scoring.last_5_avg))}</tr>`;
            html += `<tr><td class="muted">High / Low</td>${players.map(p => `<td class="cmp-cell">${p.scoring.high} / ${p.scoring.low}</td>`).join('')}</tr>`;
            html += `<tr><td class="muted">Consistency</td>${hi(players.map(p => p.scoring.consistency))}</tr>`;
            html += `<tr><td class="muted">History</td>${players.map(p => `<td class="cmp-cell">${spark(p.scoring.all_scores)}</td>`).join('')}</tr>`;

            // Pricing
            html += `<tr style="background:var(--bg-card)"><td colspan="${players.length + 1}" style="font-weight:700;color:var(--accent-cyan)">Pricing</td></tr>`;
            html += `<tr><td class="muted">Price</td>${hi(players.map(p => p.pricing.price), false)}</tr>`;
            html += `<tr><td class="muted">Breakeven</td>${hi(players.map(p => p.pricing.breakeven), false)}</tr>`;
            html += `<tr><td class="muted">Trend (3wk)</td>${players.map(p => {
                const t = p.pricing.price_trend_3wk || 0;
                return `<td class="cmp-cell" style="color:${t > 0 ? 'var(--accent-green)' : t < 0 ? 'var(--accent-red)' : ''}">${t >= 0 ? '+' : ''}$${t.toLocaleString()}</td>`;
            }).join('')}</tr>`;

            // Fixtures
            html += `<tr style="background:var(--bg-card)"><td colspan="${players.length + 1}" style="font-weight:700;color:var(--accent-cyan)">Fixtures (Next 5)</td></tr>`;
            for (let i = 0; i < 5; i++) {
                html += `<tr><td class="muted">R${players[0]?.fixtures[i]?.round || '?'}</td>`;
                players.forEach(p => {
                    const f = p.fixtures[i];
                    if (!f) html += '<td class="cmp-cell">-</td>';
                    else if (f.is_bye) html += '<td class="cmp-cell" style="color:#475569;font-weight:700">BYE</td>';
                    else html += `<td class="cmp-cell">${f.is_home ? 'vs' : '@'} ${esc(f.opponent)} <span style="color:var(--accent-yellow)">${'★'.repeat(f.dvp_stars)}</span></td>`;
                });
                html += '</tr>';
            }

            // Advanced
            html += `<tr style="background:var(--bg-card)"><td colspan="${players.length + 1}" style="font-weight:700;color:var(--accent-cyan)">Advanced</td></tr>`;
            html += `<tr><td class="muted">CBA%</td>${hi(players.map(p => p.advanced.cba_pct))}</tr>`;
            html += `<tr><td class="muted">TOG%</td>${hi(players.map(p => p.advanced.tog_pct))}</tr>`;
            html += `<tr><td class="muted">Ownership</td>${players.map(p => `<td class="cmp-cell">${p.advanced.ownership_pct ? p.advanced.ownership_pct.toFixed(0) + '%' : '-'}</td>`).join('')}</tr>`;
            html += `<tr><td class="muted">Next Bye</td>${players.map(p => `<td class="cmp-cell">${p.advanced.next_bye ? 'R' + p.advanced.next_bye : '-'}</td>`).join('')}</tr>`;

            html += '</tbody></table>';
            container.innerHTML = html;
        },

        async loadVerdict(ids) {
            const panel = document.getElementById('cmp-verdict');
            const content = document.getElementById('cmp-verdict-content');
            panel.style.display = '';
            content.innerHTML = '<div class="loading"><div class="spinner"></div><div>AI analysing...</div></div>';

            const round = App.state.config ? App.state.config.current_round : 1;
            try {
                const res = await authFetch(`${API_BASE}/api/ai/compare`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({player_ids: ids, round, season: 2026}),
                });
                const data = await res.json();
                content.innerHTML = `<p style="font-size:14px;line-height:1.7">${renderMarkdown(data.verdict)}</p>`;
            } catch (e) {
                content.innerHTML = `<div class="empty-state" style="color:var(--accent-red)">Error: ${e.message}</div>`;
            }
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
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (App.Team._swapSource) App.Team.cancelSwap();
        if (document.getElementById('optimise-overlay').style.display !== 'none') App.Team.closeOptimiser();
    }
});
